import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl
import shapefile
from psycopg import Connection

from ea import package, raw
from ea.changeset import NAMESPACE, slug
from ea.package import Draft
from ea.sources.licenses import PUBLIC_RECORD, UNLICENSED
from ea.store import DATA, connect

SOURCE = "ca.ab"
KEY = f"{SOURCE}/all"
VERSION = "4"
DAY = date(2026, 10, 19)
PAGE = "https://www.elections.ab.ca/elections/referendum/"
ORDERS = {
    109: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_109.pdf",
    110: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_110.pdf",
    160: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_160.pdf",
}
RESULTS = {
    number: f"https://officialresults.elections.ab.ca/orEDResultsReferendum2026.cfm?EventId=2026{number:02d}&MODE=exED"
    for number in range(1, 11)
}
# Each question's wording and answers as the referendum page and the Orders in Council print them.
QUESTIONS = [
    (
        1,
        (
            "Do you support the Government of Alberta taking increased control over immigration for the purposes of "
            "decreasing immigration to more sustainable levels, prioritizing economic migration and giving Albertans "
            "first priority on new employment opportunities?"
        ),
        ["Yes", "No"],
    ),
    (
        2,
        (
            "Do you support the Government of Alberta introducing a law mandating that only Canadian citizens, permanent "
            "residents and individuals with an Alberta-approved immigration status will be eligible for "
            "provincially-funded programs, such as health care, education and other social services?"
        ),
        ["Yes", "No"],
    ),
    (
        3,
        (
            "Assuming that all Canadian citizens and permanent residents continue to qualify for social support programs "
            "as they do now, do you support the Government of Alberta introducing a law requiring all individuals with a "
            "non-permanent legal immigration status to reside in Alberta for at least 12 months before qualifying for any "
            "provincially-funded social support programs?"
        ),
        ["Yes", "No"],
    ),
    (
        4,
        (
            "Assuming that all Canadian citizens and permanent residents continue to qualify for public health care and "
            "education as they do now, do you support the Government of Alberta charging a reasonable fee or premium to "
            "individuals with a non-permanent immigration status living in Alberta for their and their family’s use of "
            "the healthcare and education systems?"
        ),
        ["Yes", "No"],
    ),
    (
        5,
        (
            "Do you support the Government of Alberta introducing a law requiring individuals to provide proof of "
            "citizenship, such as a passport, birth certificate or citizenship card, to vote in an Alberta provincial "
            "election?"
        ),
        ["Yes", "No"],
    ),
    (
        6,
        (
            "Do you support the Government of Alberta working with the governments of other willing provinces to amend "
            "the Canadian Constitution to have provincial governments, and not the federal government, select the "
            "justices appointed to provincial King’s Bench and Appeal courts?"
        ),
        ["Yes", "No"],
    ),
    (
        7,
        (
            "Do you support the Government of Alberta working with the governments of other willing provinces to amend "
            "the Canadian Constitution to abolish the unelected federal Senate?"
        ),
        ["Yes", "No"],
    ),
    (
        8,
        (
            "Do you support the Government of Alberta working with the governments of other willing provinces to amend "
            "the Canadian Constitution to allow provinces to opt out of federal programs that intrude on provincial "
            "jurisdiction such as health care, education, and social services, without a province losing any of the "
            "associated federal funding for use in its social programs?"
        ),
        ["Yes", "No"],
    ),
    (
        9,
        (
            "Do you support the Government of Alberta working with the governments of other willing provinces to amend "
            "the Canadian Constitution to better protect provincial rights from federal interference by giving a "
            "province’s laws dealing with provincial or shared areas of constitutional jurisdiction priority over federal "
            "laws when the province’s laws and federal laws conflict?"
        ),
        ["Yes", "No"],
    ),
    (
        10,
        (
            "Should Alberta remain a province of Canada, or should the Government of Alberta commence the legal process "
            "required under the Canadian Constitution to hold a binding provincial referendum on whether or not Alberta "
            "should separate from Canada?"
        ),
        [
            "Alberta should remain a province of Canada.",
            (
                "The Government of Alberta should commence the legal process required under the Canadian Constitution to "
                "hold a binding provincial referendum on whether or not Alberta should separate from Canada."
            ),
        ],
    ),
]
DIVISIONS = [f"{division:02d}" for division in range(1, 88)]
# The polls a division reports besides its voting areas, with the letter that starts their unit codes and the kind
# of reporting unit each becomes. Advance polls and special ballots serve the whole division.
KINDS = {"Advance": ("A", "aggregate"), "Mobile": ("M", "mobile"), "Special Ballot": ("S", "aggregate")}


def poll_file(number: int, division: str) -> str:
    return f"{SITE}/orPollResultsReferendum2026.cfm?EventID=2026{number:02d}&ED={division}&MODE=exED"


BOUNDARIES = "https://www.elections.ab.ca/wp-content/uploads/2019Boundaries_ED-Shapefiles.zip"
MAPS_PAGE = "https://www.elections.ab.ca/resources/maps/"
VOTING_AREAS = "https://www.elections.ab.ca/uploads/EA_Voting_Area_Boundaries_2026.zip"
FIRST = date(2019, 4, 16)
RENAMED = {"17": "Calgary-Bhullar-McCall"}
SITE = "https://officialresults.elections.ab.ca"
EVENTS = {
    12: date(2008, 3, 3),
    14: date(2009, 9, 14),
    21: date(2012, 4, 23),
    28: date(2014, 10, 27),
    31: date(2015, 5, 5),
    32: date(2015, 9, 3),
    33: date(2016, 3, 22),
    40: date(2017, 12, 14),
    45: date(2018, 7, 12),
    60: date(2019, 4, 16),
    75: date(2022, 3, 15),
    95: date(2022, 11, 8),
    101: date(2023, 5, 29),
    109: date(2024, 12, 18),
    114: date(2025, 6, 23),
    130: date(2026, 9, 14),
}
GENERALS = {12, 21, 31, 60, 101}
PAGES = {40}
MAPS = {"2003": date(2004, 11, 22), "2010": date(2012, 4, 23), "2017": FIRST}
PARTIES = {
    "ADVANTAGE PARTY OF ALBERTA": "apa",
    "ALBERTA ADVANTAGE PARTY": "aap",
    "ALBERTA FIRST": "spa",
    "ALBERTA INDEPENDENCE PARTY": "aip",
    "ALBERTA LIBERAL PARTY": "lib",
    "ALBERTA NDP": "ndp",
    "ALBERTA NEW DEMOCRATS": "ndp",
    "ALBERTA NEW DEMOCRATS (NDP)": "ndp",
    "ALBERTA PARTY": "ap",
    "ALBERTA SOCIAL CREDIT PARTY": "sc",
    "COMMUNIST PARTY - ALBERTA": "cpa",
    "COMMUNIST PARTY-ALBERTA": "cpa",
    "EVERGREEN PARTY OF ALBERTA": "gpa",
    "FREEDOM CONSERVATIVE PARTY": "spa",
    "GREEN PARTY OF ALBERTA": "gpa",
    "NDP": "ndp",
    "PC PARTY": "pc",
    "PRO-LIFE ALBERTA POLITICAL ASSOC.": "papa",
    "PROGRESSIVE CONSERVATIVE PARTY": "pc",
    "PROGRESSIVE TORY PARTY OF ALBERTA": "tory",
    "REFORM PARTY": "ref",
    "REPUBLICAN PARTY OF ALBERTA": "rpa",
    "SEPARATION PARTY OF ALBERTA": "spa",
    "SOLIDARITY MOVEMENT OF ALBERTA": "sm",
    "THE BUFFALO PARTY OF ALBERTA": "bpa",
    "THE INDEPENDENCE PARTY OF ALBERTA": "tip",
    "UNITED CONSERVATIVE PARTY": "ucp",
    "WILDROSE": "wrp",
    "WILDROSE INDEPENDENCE PARTY OF ALBERTA": "wipa",
    "WILDROSE LOYALTY COALITION": "wlc",
    "WILDROSE PARTY": "wrp",
}
PARTY_NAMES = {
    "aap": "Alberta Advantage Party",
    "ag": "Green Party of Alberta",
    "aip": "Alberta Independence Party",
    "ap": "Alberta Party",
    "apa": "Advantage Party of Alberta",
    "bpa": "The Buffalo Party of Alberta",
    "cpa": "Communist Party – Alberta",
    "gpa": "Green Party of Alberta",
    "lib": "Alberta Liberal Party",
    "ndp": "Alberta New Democratic Party",
    "papa": "Pro-Life Alberta Political Association",
    "pc": "Progressive Conservative Party of Alberta",
    "ref": "Reform Party of Alberta",
    "rpa": "Republican Party of Alberta",
    "sc": "Alberta Social Credit Party",
    "sm": "Solidarity Movement of Alberta",
    "spa": "Freedom Conservative Party",
    "tip": "The Independence Party of Alberta",
    "tory": "Progressive Tory Party of Alberta",
    "ucp": "United Conservative Party",
    "wipa": "Wildrose Independence Party of Alberta",
    "wlc": "Wildrose Loyalty Coalition",
    "wrp": "Wildrose Party",
}
FORMER_NAMES = {"spa": ["Separation Party of Alberta", "Alberta First"], "gpa": ["Evergreen Party of Alberta"]}
BALLOTS_NOTE = (
    "Elections Alberta reports valid, spoiled, declined, and rejected ballots for each electoral division. "
    "Ballots cast here add all four, and invalid ballots add the spoiled, declined, and rejected ones. "
    "A declined ballot is one an elector returned to the officials without marking it."
)
POLLS_NOTE = (
    "Elections Alberta reports each question for every voting area on election day, and for each electoral "
    "division's advance polls, mobile polls, and special ballots, with valid, spoiled, declined, and rejected "
    "ballots. Ballots cast here add all four, and invalid ballots add the spoiled, declined, and rejected ones. "
    "Advance polls and special ballots serve the whole division and are stored as aggregate units of it; their "
    "electors are on the voting areas' lists. The division totals are the sum of its polls and match the province "
    "workbook."
)
SHARED_NOTE = (
    "At {count} voting places, voting areas shared a ballot box, and Elections Alberta reports its votes against one "
    "of those areas and zeros on every question for the others: {places}. Those areas are stored as counted with the "
    "one that holds the votes, with their electors and no ballots of their own, so its ballots can exceed its own "
    "electors."
)
COLORS = {
    "ndp": "#f37021",
    "ucp": "#1f4e99",
    "pc": "#1a57a6",
    "wrp": "#2e8b57",
    "lib": "#d71920",
    "ap": "#00aeef",
    "gpa": "#3d9b35",
}
PARSED = DATA / "parsed" / SOURCE


def order(number: int) -> int:
    return 110 if number <= 5 else 109 if number <= 9 else 160


def event_page(event: int) -> str:
    return f"{SITE}/orResultsPGE.cfm?EventId={event}"


def winners_file(event: int) -> str:
    return f"{SITE}/orWinningCandidates.cfm?MODE=exCandidates&EventId={event}"


def division_page(event: int, division: str) -> str:
    return f"{SITE}/orResultsED.cfm?ED={division}&EventId={event}"


def division_file(event: int, division: str) -> str:
    if event == 130:
        return f"{SITE}/orResultsED2026BE.cfm?MODE=exED&EventId={event}"
    return f"{SITE}/orResultsED.cfm?MODE=exED&ED={division}&EventId={event}"


def divisions(event: int) -> list[str]:
    page = raw.cached(SOURCE, event_page(event)).path.read_text(errors="replace")
    if event == 130:
        return sorted(set(re.findall(r'orResultsED2026BE\.cfm\?EventId=130">(\d+)<', page)))
    return sorted(set(re.findall(rf"orResultsED\.cfm\?ED=(\d+)&EventId={event}\b", page)))


def map_of(event: int) -> str:
    return "2003" if event in (12, 14) else "2010" if EVENTS[event] < FIRST else "2017"


def area_code(event: int, division: int) -> str:
    vintage = map_of(event)
    return str(division) if vintage == "2017" else f"{vintage}:{division}"


def titled(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if text != text.upper():
        return text
    words = re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), text.lower())
    words = re.sub(r"\b(Mc|O')([a-z])", lambda m: m.group(1) + m.group(2).upper(), words)
    return re.sub(r"\b(Of|And)\b", lambda m: m.group(0).lower(), words).replace("'S ", "'s ")


def party_of(event: int, text: str) -> str:
    return "ag" if event == 12 and text == "GREEN PARTY OF ALBERTA" else PARTIES[text]


def count(cell: str | None) -> int:
    return int(re.sub(r"[,\s]", "", (cell or "0").split("\n")[0]) or 0)


def spoken(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])}{',' if len(items) > 2 else ''} and {items[-1]}"


def fetch() -> None:
    for event in EVENTS:
        raw.fetch(SOURCE, event_page(event), suffix=".html")
        raw.fetch(SOURCE, winners_file(event), suffix=".xlsx")
        for division in divisions(event):
            raw.fetch(SOURCE, division_file(event, division), suffix=".xlsx")
            if event in PAGES:
                raw.fetch(SOURCE, division_page(event, division), suffix=".html")
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for url in ORDERS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in RESULTS.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    raw.fetch_many(
        SOURCE, [poll_file(number, division) for number in RESULTS for division in DIVISIONS], suffix=".xls", workers=4
    )
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")
    raw.fetch(SOURCE, MAPS_PAGE, suffix=".html")
    raw.fetch(SOURCE, VOTING_AREAS, suffix=".zip")


def totals(event: int, division: str) -> tuple[list[str], int, list[int], int, int, int, int]:
    if event in PAGES:
        page = raw.cached(SOURCE, division_page(event, division)).path.read_text(errors="replace")
        lines = [line.strip() for line in re.sub(r"<[^>]*>", "\n", page).split("\n") if line.strip()]
        start = lines.index("Names on List") + 1
        end = lines.index("Valid Ballots Cast", start)
        labels = [f"{name}\n{code}" for name, code in zip(lines[start:end:2], lines[start + 1 : end : 2], strict=True)]
        if lines[end : end + 4] != ["Valid Ballots Cast", "Spoiled Ballots", "Declined Ballots", "Rejected Ballots"]:
            raise ValueError(f"event {event} division {division}: unexpected columns {lines[end : end + 4]}")
        first = next(i for i in range(end + 4, len(lines)) if re.fullmatch(r"[\d,]+", lines[i]))
        numbers = [count(n) for n in lines[first : first + len(labels) + 5]]
        valid, spoiled, declined, rejected = numbers[-4:]
        return labels, numbers[0], numbers[1:-4], valid, spoiled, declined, rejected
    sheet = pl.read_excel(
        raw.cached(SOURCE, division_file(event, division)).path, has_header=False, infer_schema_length=0
    )
    title = sheet.row(0)[0]
    if not re.match(rf"Official Poll Results - {division} -", title):
        raise ValueError(f"event {event} division {division}: {title}")
    header = next(r for r in sheet.iter_rows() if any(c in ("Names on List", "Number of Electors") for c in r))
    first = header.index("Names on List" if "Names on List" in header else "Number of Electors")
    valid = header.index("Valid Ballots Cast")
    total = next(
        r
        for r in sheet.iter_rows()
        if any(c and c.startswith("Total") for c in r[: first + 1]) and r[first] is not None
    )
    spoiled, declined, rejected = (
        count(total[header.index(f"{kind} Ballots")]) for kind in ("Spoiled", "Declined", "Rejected")
    )
    return (
        list(header[first + 1 : valid]),
        count(total[first]),
        [count(c) for c in total[first + 1 : valid]],
        count(total[valid]),
        spoiled,
        declined,
        rejected,
    )


def province_totals(event: int) -> dict[str, tuple[int, int]]:
    page = raw.cached(SOURCE, event_page(event)).path.read_text(errors="replace")
    head = re.findall(r'<Th style="text-align:CENTER;">\s*([A-Z][A-Z0-9\-]*)', page)
    row = page[page.index('class="showTotal"') :]
    cells = [
        re.findall(r"\d[\d,.]*%?", re.sub(r"<[^>]*>|&nbsp;", " ", "<" + c))
        for c in re.split(r"<TD", row[: row.index("</tr>")], flags=re.IGNORECASE)[3:-1]
    ]
    return {code: (count(numbers[0]), int(numbers[2])) for code, numbers in zip(head[: len(cells)], cells, strict=True)}


def parse_elections() -> None:
    contests = []
    candidates = []
    party_names = []
    for event, day in EVENTS.items():
        page = raw.cached(SOURCE, event_page(event)).path.read_text(errors="replace")
        codes = {c: n.strip() for c, n in re.findall(r"<TD>([A-Z][A-Z0-9\-]*)</TD>\s*<TD>([^<]+)</TD>", page)}
        codes.pop("IND", None)
        for code, text in codes.items():
            party_names.append(
                {"event": event, "date": day, "code": code, "name": text, "party": party_of(event, text)}
            )
        winners = pl.read_excel(raw.cached(SOURCE, winners_file(event)).path, has_header=False, infer_schema_length=0)
        elected = {int(r[0]): (r[1], r[2], count(r[4])) for r in winners.iter_rows() if r[0] and r[0].isdigit()}
        listed = divisions(event)
        if sorted(elected) != sorted(int(d) for d in listed):
            raise ValueError(f"event {event}: winners for {sorted(elected)}, divisions {listed}")
        for division in listed:
            number = int(division)
            labels, registered, votes, valid, spoiled, declined, rejected = totals(event, division)
            if sum(votes) != valid:
                raise ValueError(f"event {event} division {division}: candidates sum to {sum(votes)}, valid {valid}")
            ed_name, winner, winner_votes = elected[number]
            top = max(votes)
            names = [titled(label.rpartition("\n")[0]) for label in labels]
            if votes.count(top) != 1 or names[votes.index(top)] != titled(winner):
                raise ValueError(
                    f"event {event} division {division}: winner {winner}, top is {names[votes.index(top)]}"
                )
            if winner_votes != top:
                print(f"event {event} division {division}: winners list gives {winner} {winner_votes}, division {top}")
            contests.append(
                {
                    "event": event,
                    "date": day,
                    "division": number,
                    "area": area_code(event, number),
                    "name": titled(ed_name),
                    "registered": registered,
                    "valid": valid,
                    "spoiled": spoiled,
                    "declined": declined,
                    "rejected": rejected,
                    "url": division_page(event, division) if event in PAGES else division_file(event, division),
                }
            )
            for position, (cell, value) in enumerate(zip(labels, votes, strict=True), 1):
                label, _, code = cell.rpartition("\n")
                text = codes.get(code)
                if code != "IND" and text is None:
                    raise ValueError(f"event {event} division {division}: party {code} not listed")
                candidates.append(
                    {
                        "event": event,
                        "date": day,
                        "division": number,
                        "position": position,
                        "name": titled(label),
                        "code": code,
                        "party": None if text is None else party_of(event, text),
                        "votes": value,
                        "elected": value == top,
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    contests_frame = pl.DataFrame(contests)
    candidates_frame = pl.DataFrame(candidates)
    parties_frame = pl.DataFrame(party_names)
    for event in EVENTS:
        held = {
            code: (votes, seats)
            for code, votes, seats in candidates_frame.filter(pl.col("event") == event)
            .group_by("code")
            .agg(pl.col("votes").sum(), pl.col("elected").sum())
            .iter_rows()
        }
        published = province_totals(event)
        if {c: v for c, (v, _) in held.items()} != {c: v for c, (v, _) in published.items()}:
            raise ValueError(f"event {event}: party votes {held} differ from {published}")
        if held != published:
            print(f"event {event}: seats by party {held} where the province table gives {published}")
    for (event,), rows in contests_frame.group_by("event"):
        if event in GENERALS and rows.height != (83 if event == 12 else 87):
            raise ValueError(f"event {event}: {rows.height} divisions")
    contests_frame.write_parquet(PARSED / "contests.parquet")
    candidates_frame.write_parquet(PARSED / "candidates.parquet")
    parties_frame.write_parquet(PARSED / "parties.parquet")
    print(
        f"{contests_frame.height} contests, {candidates_frame.height} candidates, {candidates_frame['elected'].sum()} elected"
    )


def parse_polls(results: pl.DataFrame) -> pl.DataFrame:
    """Each question's results by voting area, advance poll, mobile poll, and special ballot count in each division,
    checked against the workbook's subtotals and total row and against the division's row in the province workbook."""
    rows: list[dict[str, object]] = []
    names = ("electors", "first", "second", "valid", "spoiled", "declined", "rejected")
    for number in RESULTS:
        for division in DIVISIONS:
            # The workbooks are xlsx files served as application/vnd.ms-excel, so they are read from their bytes.
            sheet = pl.read_excel(
                raw.cached(SOURCE, poll_file(number, division)).path.read_bytes(),
                has_header=False,
                infer_schema_length=0,
            ).rows()
            where = f"question {number} division {division}"
            if not (sheet[2][0] or "").startswith(f"{division} - ") or not (sheet[3][0] or "").startswith(
                f"Question {number}:"
            ):
                raise ValueError(f"{where}: titled {sheet[2][0]!r}, {sheet[3][0]!r}")
            head = next(i for i, row in enumerate(sheet) if row[0] == "Voting Place / Voting Area")
            if [re.sub(r"\s+", " ", cell or "") for cell in sheet[head][2:10]] != [
                "Type",
                "Number of Electors",
                *(("Option 1", "Option 2") if number == 10 else ("Yes", "No")),
                "Valid Ballots Cast",
                "Spoiled Ballots",
                "Declined Ballots",
                "Rejected Ballots",
            ]:
                raise ValueError(f"{where}: columns {sheet[head]}")
            end = next(i for i in range(head + 1, len(sheet)) if (sheet[i][0] or "").endswith(" Subtotal"))
            seen: dict[str, int] = {}
            found = []
            for code, place, kind, electors, *cells in (row[:10] for row in sheet[head + 1 : end]):
                first, second, valid, spoiled, declined, rejected = (count(cell) for cell in cells)
                if kind == "Election Day":
                    if not re.fullmatch(r"\d{3}", code or "") or electors is None:
                        raise ValueError(f"{where}: voting area {code!r} at {place!r}")
                    key = code
                elif kind in KINDS and not code and electors is None:
                    seen[kind] = seen.get(kind, 0) + 1
                    key = f"{KINDS[kind][0]}{seen[kind]}"
                else:
                    raise ValueError(f"{where}: row {code!r} {place!r} {kind!r}")
                if first + second != valid:
                    raise ValueError(f"{where}: {key} answers do not add up to its valid ballots")
                found.append(
                    {
                        "question": number,
                        "division": int(division),
                        "key": key,
                        "place": re.sub(r"\s+", " ", place or "").strip(),
                        "type": kind,
                        "electors": None if electors is None else count(electors),
                        "first": first,
                        "second": second,
                        "valid": valid,
                        "spoiled": spoiled,
                        "declined": declined,
                        "rejected": rejected,
                    }
                )
            frame = pl.DataFrame(found)
            totals = {str(row[0]): row for row in sheet[end:] if row[0]}
            for kind in ("Election Day", *KINDS):
                part = frame.filter(pl.col("type") == kind)
                printed = [count(cell) for cell in totals[f"{kind} Subtotal"][3:10]]
                summed = [0 if part[name].is_null().all() else part[name].sum() for name in names]
                if printed != summed:
                    raise ValueError(f"{where}: {kind} rows sum to {summed}, subtotal {printed}")
            printed = [count(cell) for cell in totals["Total Valid Vote"][3:10]]
            if printed != [frame[name].sum() for name in names]:
                raise ValueError(f"{where}: rows do not sum to the total row")
            province = results.filter((pl.col("question") == number) & (pl.col("division") == int(division))).row(
                0, named=True
            )
            if [province[name] for name in ("electors", "first", "second")] != printed[:3]:
                raise ValueError(f"{where}: total row {printed[:3]} differs from the province workbook")
            rows.extend(frame.to_dicts())
    polls = pl.DataFrame(rows).with_columns(
        cast=pl.col("valid") + pl.col("spoiled") + pl.col("declined") + pl.col("rejected")
    )
    # Every question lists the same polls with the same electors in each division.
    shape = polls.select("division", "key", "place", "type", "electors").unique()
    if shape.height != polls.filter(pl.col("question") == 1).height:
        raise ValueError("the questions list different polls")
    # Where voting areas shared a ballot box, Elections Alberta reports its votes against the first voting area at
    # that voting place with ballots and zeros for the others: a voting area with no ballots on any question at a
    # place where another area has them was counted with the first such area.
    areas = (
        polls.filter(pl.col("type") == "Election Day")
        .group_by("division", "key", "place")
        .agg(empty=pl.col("cast").max() == 0)
    )
    hosts = (
        areas.filter(~pl.col("empty"))
        .group_by("division", "place")
        .agg(host=pl.col("key").min())
        .join(areas.filter("empty"), on=["division", "place"])
        .select("division", "key", "host")
    )
    print(
        f"{polls.height} poll rows; {hosts.height} voting areas counted with another at "
        f"{hosts.select('division', 'host').n_unique()} voting places"
    )
    return polls.join(hosts, on=["division", "key"], how="left")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    parse_elections()
    rows = []
    for number, url in RESULTS.items():
        sheet = pl.read_excel(raw.cached(SOURCE, url).path, has_header=False, infer_schema_length=0)
        divisions = sheet.filter(pl.col("column_0").str.contains(r"^\d{2}$"))
        total = sheet.filter(pl.col("column_0") == "Total Valid Vote").row(0)
        if divisions.height != 87:
            raise ValueError(f"question {number}: {divisions.height} divisions")
        for column in ("column_3", "column_4", "column_5"):
            if divisions[column].cast(pl.Int64).sum() != int(total[int(column[-1])]):
                raise ValueError(f"question {number}: {column} does not sum to the total")
        rows.extend(
            {
                "question": number,
                "division": int(code),
                "name": name,
                "electors": int(electors),
                "first": int(first),
                "second": int(second),
            }
            for code, name, _, electors, first, second, _ in divisions.iter_rows()
        )
    results = pl.DataFrame(rows)
    polls = parse_polls(results)
    polls.write_parquet(PARSED / "polls.parquet")
    with zipfile.ZipFile(raw.cached(SOURCE, BOUNDARIES).path) as z:
        parts = {
            name.rsplit(".", 1)[1].lower(): io.BytesIO(z.read(name))
            for name in z.namelist()
            if name.rsplit(".", 1)[1].lower() in ("shp", "shx", "dbf")
        }
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    records = [
        (str(record["EDNumber20"]), record["EDName2017"], json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        if record is not None and shape is not None
    ]
    # Reprojecting the same shapefile on another PROJ build moves vertices by 10 cm to 3 m, so an outline the archive
    # already holds with the same rings and points, lying within about 10 m of the one rebuilt here, is kept as
    # stored, and a rebuild rewrites no outline.
    with connect() as conn:
        shapes = pl.DataFrame(
            conn.execute(
                """with ed as (
                     select code, name, st_multi(st_collectionextract(st_makevalid(st_transform(
                              st_setsrid(st_geomfromgeojson(geojson), 3401), 4326)), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)),
                   stored as (
                     select c.code, g.geom, g.label_point from area_code c
                     join area a on a.id = c.area_id and a.scheme_code = 'ca.ab'
                     join area_geometry g on g.area_id = a.id and g.valid_from = %s
                     join changeset s on s.id = g.changeset_id and s.pipeline = %s
                     where c.valid_from = %s),
                   kept as (
                     select ed.code, ed.name, ed.geom, s.geom is not null and st_numgeometries(s.geom) = st_numgeometries(ed.geom)
                              and st_npoints(s.geom) = st_npoints(ed.geom)
                              and st_hausdorffdistance(s.geom, ed.geom) < 1e-4 as same, s.geom as old, s.label_point
                     from ed left join stored s using (code))
                   select code, name, encode(st_asewkb(case when same then old else geom end), 'hex'),
                          encode(st_asewkb(case when same then label_point else st_pointonsurface(geom) end), 'hex'),
                          st_x(st_centroid(geom)), st_y(st_centroid(geom))
                   from kept order by code::int""",
                [*(list(column) for column in zip(*records, strict=True)), FIRST, SOURCE, FIRST],
            ).fetchall(),
            schema=["code", "name", "geom", "label_point", "x", "y"],
            orient="row",
        )
    if not shapes.filter(
        (pl.col("x") < -120.1) | (pl.col("x") > -109.9) | (pl.col("y") < 49) | (pl.col("y") > 60)
    ).is_empty():
        raise ValueError("division centroids fall outside Alberta")
    names = dict(results.select(pl.col("division").cast(pl.String), "name").unique().iter_rows())
    shapes = shapes.with_columns(
        former=pl.when(pl.col("code").is_in(list(RENAMED))).then(pl.col("name")),
        name=pl.col("code").replace_strict(RENAMED, default=pl.col("name"), return_dtype=pl.String),
    )
    for code, name in shapes.select("code", "name").iter_rows():
        if re.sub(r"\W", "", names[code]).upper() != re.sub(r"\W", "", name).upper():
            raise ValueError(f"division {code}: {name} in the boundaries, {names[code]} in the results")
    results.write_parquet(PARSED / "results.parquet")
    shapes.drop("x", "y").write_parquet(PARSED / "shapes.parquet")
    print(f"{results.height} question rows, {shapes.height} divisions")
    print(build())


def emit() -> None:
    package.run("load", [KEY])


def retrieved(conn: Connection) -> Callable[[raw.Artifact], raw.Artifact]:
    """A file the cache holds with the same bytes as a file the archive already cites keeps the retrieval time the
    archive recorded for it, so a fresh copy of the cache rebuilds the same citation."""
    stored: dict[tuple[str, str], datetime] = {
        (str(url), str(digest)): at
        for url, digest, at in conn.execute(
            """select a.url, a.sha256, a.retrieved_at from source_artifact a
               join changeset c on c.id = a.changeset_id where c.pipeline = %s""",
            [SOURCE],
        )
    }

    def pin(artifact: raw.Artifact) -> raw.Artifact:
        at = stored.get((artifact.url, artifact.sha256))
        return artifact if at is None else replace(artifact, retrieved_at=at)

    return pin


def build() -> Path:
    """Write the package ca.ab/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them."""
    results = pl.read_parquet(PARSED / "results.parquet")
    shapes = pl.read_parquet(PARSED / "shapes.parquet")
    polls = pl.read_parquet(PARSED / "polls.parquet")
    with connect() as conn:
        pin = retrieved(conn)
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Alberta's 2026 referendum by voting area, its general elections from 2008 to 2023, and its "
                "by-elections from 2009 to 2026 by electoral division, with official results from Elections Alberta"
            ),
            polity="CA-AB",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        cs.accept(
            "accounting.turnout",
            "Elections Alberta's referendum workbooks print more ballots than electors on some questions in 16 voting "
            "areas: 5 that hold the votes of a ballot box shared with other areas, which the result sets' note lists, "
            "and 11 others, where electors who registered at the poll can exceed the list; stored as printed",
        )
        alberta = cs.polity("CA-AB")
        if alberta in cs.minted:
            raise ValueError("run ca.elections first: Alberta must exist")
        elections = cs.source("Elections Alberta", "2026 Alberta Referendum")
        cs.add(
            "source",
            id=elections,
            kind="official_body",
            publisher="Elections Alberta",
            title="2026 Alberta Referendum",
            url=PAGE,
            license=PUBLIC_RECORD,
        )
        cs.artifact(pin(raw.cached(SOURCE, PAGE)), elections)
        orders = {}
        for number, url in ORDERS.items():
            orders[number] = cs.source("Alberta King's Printer", f"Order in Council {number}/2026")
            cs.add(
                "source",
                id=orders[number],
                kind="official_body",
                publisher="Alberta King's Printer",
                title=f"Order in Council {number}/2026",
                url=url,
                license=UNLICENSED,
            )
            cs.artifact(pin(raw.cached(SOURCE, url)), orders[number])
        official = cs.source("Elections Alberta", "2026 Referendum Official Results by Electoral Division")
        cs.add(
            "source",
            id=official,
            kind="official_body",
            publisher="Elections Alberta",
            title="2026 Referendum Official Results by Electoral Division",
            url="https://officialresults.elections.ab.ca/",
            license=PUBLIC_RECORD,
        )
        for url in RESULTS.values():
            cs.artifact(pin(raw.cached(SOURCE, url)), official)
        by_area = cs.source("Elections Alberta", "2026 Referendum Official Poll Results")
        cs.add(
            "source",
            id=by_area,
            kind="official_body",
            publisher="Elections Alberta",
            title="2026 Referendum Official Poll Results",
            url="https://officialresults.elections.ab.ca/",
            license=PUBLIC_RECORD,
        )
        for number in RESULTS:
            for division in DIVISIONS:
                cs.artifact(pin(raw.cached(SOURCE, poll_file(number, division))), by_area)
        boundaries = cs.source("Elections Alberta", "2019 Electoral Division Boundaries (shapefiles)")
        cs.add(
            "source",
            id=boundaries,
            kind="official_body",
            publisher="Elections Alberta",
            title="2019 Electoral Division Boundaries (shapefiles)",
            url="https://open.alberta.ca/opendata/d2eff235-4c11-416f-b4e2-791c305594b1",
            license="Open Government Licence – Alberta",
            license_url="https://open.alberta.ca/licence",
        )
        cs.artifact(pin(raw.cached(SOURCE, BOUNDARIES)), boundaries)
        province = cs.area("ca.province", "48")
        if province in cs.minted:
            raise ValueError("run ca.elections first: Alberta's province area must exist")
        cs.add("scheme", code="ca.ab", polity_id=alberta, kind="electoral_district", rank=2, noun="electoral division")
        cs.add("boundary_set", scheme_code="ca.ab", vintage="2017", valid_from=FIRST, valid_to=None, as_of=FIRST)
        areas = {}
        units = {}
        for code, division, geom, label_point, former in shapes.iter_rows():
            area = areas[int(code)] = cs.area("ca.ab", code, FIRST)
            cs.add("area", id=area, scheme_code="ca.ab")
            cs.add("area_code", area_id=area, code=code, valid_from=FIRST, valid_to=None)
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, division)
            if former:
                cs.add("name", entity_id=area, text=former, lang="en", kind="historical", is_preferred=False)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=FIRST,
                valid_to=None,
                geom=geom,
                label_point=label_point,
                quality="official",
                source_id=boundaries,
            )
            units[int(code)] = cs.unit("ca.ab", f"{DAY}:{code}", DAY)
            cs.add(
                "reporting_unit",
                id=units[int(code)],
                polity_id=alberta,
                scheme_code="ca.ab",
                code=f"{DAY}:{code}",
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
        cs.add("scheme", code="ca.ab_va", polity_id=alberta, kind="precinct", rank=3, noun="voting area")
        stations = polls.filter(pl.col("question") == 1)
        places = {
            (division, key): cs.unit("ca.ab_va", f"{DAY}:{division:02d}:{key}", DAY)
            for division, key in stations.select("division", "key").iter_rows()
        }
        for row in stations.iter_rows(named=True):
            kind = "standard" if row["type"] == "Election Day" else KINDS[row["type"]][1]
            cs.add(
                "reporting_unit",
                id=places[row["division"], row["key"]],
                polity_id=alberta,
                scheme_code="ca.ab_va",
                code=f"{DAY}:{row['division']:02d}:{row['key']}",
                parent_id=units[row["division"]],
                counted_with_id=None if row["host"] is None else places[row["division"], row["host"]],
                area_relation="equals" if kind == "aggregate" else "within",
                area_id=areas[row["division"]],
                kind=kind,
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
        division_names = dict(shapes.select(pl.col("code").cast(pl.Int64), "name").iter_rows())
        shared = [
            f"{place} in {division_names[division]} (voting area {host}, with {listed(sorted(keys))})"
            for division, place, host, keys in stations.filter(pl.col("host").is_not_null())
            .group_by("division", "place", "host")
            .agg("key")
            .sort("division", "host")
            .iter_rows()
        ]
        shared_note = SHARED_NOTE.format(count=len(shared), places="; ".join(shared))
        system = cs.key(f"{SOURCE}.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=alberta, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Alberta referendum")
        election = cs.key(f"{SOURCE}.election", str(DAY))
        cs.add("election", id=election, polity_id=alberta, kind="referendum", date_from=DAY, date_to=DAY, status="held")
        name(election, "2026 Alberta referendum")
        cs.add("slug", path=f"ca/ab/{DAY}-referendum", entity_id=election)
        cs.add("note", entity_id=election, kind="methodology", body_md=POLLS_NOTE)
        for number, text, answers in QUESTIONS:
            contest = cs.key(f"{SOURCE}.contest", f"{DAY}:{number}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="optional_referendum")
            name(contest, f"Question {number}")
            cs.add("slug", path=f"ca/ab/{DAY}-referendum/question-{number}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", f"{DAY}:{number}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=by_area,
                status="active",
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=shared_note)
            rows = results.filter(pl.col("question") == number)
            totals = {"first": rows["first"].sum(), "second": rows["second"].sum()}
            # A voting area counted with another keeps its electors and has no ballots of its own.
            values = []
            for row in polls.filter(pl.col("question") == number).iter_rows(named=True):
                unit = places[row["division"], row["key"]]
                if row["electors"] is not None:
                    values.append({"unit": unit, "option": None, "measure": 2, "value": row["electors"]})
                if row["host"] is None:
                    invalid = row["spoiled"] + row["declined"] + row["rejected"]
                    values.extend(
                        {"unit": unit, "option": None, "measure": measure, "value": value}
                        for measure, value in ((3, row["valid"] + invalid), (4, row["valid"]), (5, invalid))
                    )
            for position, (label, column) in enumerate(zip(answers, totals, strict=True), 1):
                option = cs.key(f"{SOURCE}.option", f"{DAY}:{number}:{position}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="won" if totals[column] == max(totals.values()) else "lost",
                )
                cs.add("declared", result_set_id=result_set, option_id=option, measure=1, value=totals[column])
                values.extend(
                    {"unit": places[division, key], "option": option, "measure": 1, "value": votes}
                    for division, key, votes in polls.filter((pl.col("question") == number) & pl.col("host").is_null())
                    .select("division", "key", column)
                    .iter_rows()
                )
            cs.frame(
                "result_value",
                pl.DataFrame(
                    {
                        "result_set_id": [str(result_set)] * len(values),
                        "unit_id": [str(v["unit"]) for v in values],
                        "option_id": [None if v["option"] is None else str(v["option"]) for v in values],
                        "channel": [0] * len(values),
                        "measure": [v["measure"] for v in values],
                        "count_round": [0] * len(values),
                        "flags": [0] * len(values),
                        "value": [v["value"] for v in values],
                    },
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
                ),
            )
            for kind, body, url, source in (
                ("text", text, PAGE, elections),
                ("link", None, ORDERS[order(number)], orders[order(number)]),
            ):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"{SOURCE}\x1fballot_question_text\x1f{contest}\x1f{kind}"),
                    contest_id=contest,
                    kind=kind,
                    lang="en",
                    body=body,
                    url=url,
                    source_id=source,
                )
        assembly_elections(cs, alberta, province, name, pin)
        return cs.write()


def assembly_elections(
    cs: Draft,
    alberta: UUID,
    province: UUID,
    name: Callable[[object, str], None],
    pin: Callable[[raw.Artifact], raw.Artifact],
) -> None:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    parties = pl.read_parquet(PARSED / "parties.parquet")

    def other(entity: object, text: str, kind: str) -> None:
        cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=False)

    assembly = cs.key(f"{SOURCE}.body", "legislative_assembly")
    cs.add("body", id=assembly, polity_id=alberta, kind="unicameral")
    name(assembly, "Legislative Assembly of Alberta")
    cs.add("body_size", body_id=assembly, seats=83, valid_from=MAPS["2003"], valid_to=MAPS["2010"])
    cs.add("body_size", body_id=assembly, seats=87, valid_from=MAPS["2010"], valid_to=None)
    fptp = cs.key(f"{SOURCE}.electoral_system", "fptp")
    cs.add("electoral_system", id=fptp, polity_id=alberta, rules={"formula": "fptp"}, rules_version=1)
    name(fptp, "First past the post")

    areas: dict[str, UUID] = {}
    for vintage, end in (("2003", MAPS["2010"]), ("2010", FIRST)):
        start = MAPS[vintage]
        cs.add("boundary_set", scheme_code="ca.ab", vintage=vintage, valid_from=start, valid_to=end, as_of=start)
        old = contests.filter(pl.col("area").str.starts_with(f"{vintage}:")).sort("date")
        for code, label in old.group_by("area", maintain_order=True).agg(pl.col("name").last()).iter_rows():
            area = areas[code] = cs.area("ca.ab", code, start)
            cs.add("area", id=area, scheme_code="ca.ab")
            cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, label)
    for code in contests.filter(~pl.col("area").str.contains(":"))["area"].unique():
        areas[code] = cs.area("ca.ab", code, FIRST)

    party_ids: dict[str, UUID] = {}
    for key in sorted(parties["party"].unique()):
        party_ids[key] = cs.key(f"{SOURCE}.party", key)
        cs.add("party", id=party_ids[key], kind="party", polity_id=alberta)
        name(party_ids[key], PARTY_NAMES[key])
        for code in sorted(parties.filter(pl.col("party") == key)["code"].unique()):
            other(party_ids[key], code, "abbreviation")
        for former in FORMER_NAMES.get(key, []):
            other(party_ids[key], former, "historical")
        if key in COLORS:
            cs.add("party_color", party_id=party_ids[key], color=COLORS[key])

    columns = {
        "result_set_id": pl.String,
        "unit_id": pl.String,
        "option_id": pl.String,
        "channel": pl.Int16,
        "measure": pl.Int16,
        "count_round": pl.Int16,
        "flags": pl.Int16,
        "value": pl.Int64,
    }
    for event, day in EVENTS.items():
        general = event in GENERALS
        kind = "general" if general else "by-election"
        rows = contests.filter(pl.col("event") == event).sort("division")
        labels = rows["name"].to_list()
        source = cs.source(
            "Elections Alberta",
            f"Official Results: {'Provincial General Election' if general else 'By-Election'}, {spoken(day)}",
        )
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Elections Alberta",
            title=f"Official Results: {'Provincial General Election' if general else 'By-Election'}, {spoken(day)}",
            url=event_page(event),
            license=PUBLIC_RECORD,
        )
        cs.artifact(pin(raw.cached(SOURCE, event_page(event))), source)
        cs.artifact(pin(raw.cached(SOURCE, winners_file(event))), source)
        election = cs.key(f"{SOURCE}.election", day.isoformat())
        cs.add(
            "election",
            id=election,
            polity_id=alberta,
            kind="general" if general else "by_election",
            date_from=day,
            date_to=day,
            status="held",
        )
        if general:
            name(election, f"{day.year} Alberta general election")
        else:
            name(election, f"{listed(labels)} provincial by-election{'s' if len(labels) > 1 else ''}, {spoken(day)}")
        cs.add("slug", path=f"ca/ab/{day}-{kind}", entity_id=election)
        cs.add("note", entity_id=election, kind="methodology", body_md=BALLOTS_NOTE)
        values = []
        for row in rows.iter_rows(named=True):
            number = row["division"]
            cs.artifact(pin(raw.cached(SOURCE, row["url"])), source)
            contest = cs.key(f"{SOURCE}.contest", f"{day}:{number}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=assembly,
                district_area_id=areas[row["area"]],
                magnitude=1,
                electoral_system_id=fptp,
            )
            name(contest, f"{day.year} {'election' if general else 'by-election'} in {row['name']}")
            cs.add("slug", path=f"ca/ab/{day}-{kind}/{slug(row['name'])}", entity_id=contest)
            unit = cs.unit("ca.ab", f"{day}:{number}", day)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=alberta,
                scheme_code="ca.ab",
                code=f"{day}:{number}",
                area_relation="equals",
                area_id=areas[row["area"]],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{number}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=True,
                completeness=1,
            )
            invalid = row["spoiled"] + row["declined"] + row["rejected"]
            for measure, value in (
                (2, row["registered"]),
                (3, row["valid"] + invalid),
                (4, row["valid"]),
                (5, invalid),
            ):
                values.append((result_set, unit, None, measure, value))
            for candidate in candidates.filter((pl.col("event") == event) & (pl.col("division") == number)).iter_rows(
                named=True
            ):
                position = candidate["position"]
                option = cs.key(f"{SOURCE}.option", f"{day}:{number}:{position}")
                party = None if candidate["party"] is None else party_ids[candidate["party"]]
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=candidate["name"],
                )
                person = cs.key(f"{SOURCE}.person", f"{day}:{number}:{position}")
                cs.add("person", id=person)
                name(person, candidate["name"])
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{day}:{number}:{position}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                if candidate["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                values.append((result_set, unit, option, 1, candidate["votes"]))
        cs.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": [str(v[0]) for v in values],
                    "unit_id": [str(v[1]) for v in values],
                    "option_id": [None if v[2] is None else str(v[2]) for v in values],
                    "channel": [0] * len(values),
                    "measure": [v[3] for v in values],
                    "count_round": [0] * len(values),
                    "flags": [0] * len(values),
                    "value": [v[4] for v in values],
                },
                schema=columns,
            ),
        )
