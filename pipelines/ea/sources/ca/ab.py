import io
import json
import re
import zipfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.licenses import PUBLIC_RECORD, UNLICENSED
from ea.store import DATA, connect

SOURCE = "ca.ab"
VERSION = "3"
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
BOUNDARIES = "https://www.elections.ab.ca/wp-content/uploads/2019Boundaries_ED-Shapefiles.zip"
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
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")


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


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    parse_elections()
    if not (PARSED / "questions.json").exists():
        (PARSED / "questions.json").write_text((DATA / "tmp" / "nh-ab" / "questions.json").read_text())
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
    with connect() as conn:
        shapes = pl.DataFrame(
            conn.execute(
                """with ed as (
                     select code, name, st_multi(st_collectionextract(st_makevalid(st_transform(
                              st_setsrid(st_geomfromgeojson(geojson), 3401), 4326)), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson))
                   select code, name, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex'),
                          st_x(st_centroid(geom)), st_y(st_centroid(geom))
                   from ed order by code::int""",
                [list(column) for column in zip(*records, strict=True)],
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


def emit() -> Path:
    questions = json.loads((PARSED / "questions.json").read_text())["questions"]
    results = pl.read_parquet(PARSED / "results.parquet")
    shapes = pl.read_parquet(PARSED / "shapes.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Alberta's 2026 referendum, its general elections from 2008 to 2023, and its by-elections from 2009 "
                "to 2026, with official results by electoral division from Elections Alberta"
            ),
            polity="CA-AB",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

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
        cs.artifact(raw.cached(SOURCE, PAGE), elections)
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
            cs.artifact(raw.cached(SOURCE, url), orders[number])
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
            cs.artifact(raw.cached(SOURCE, url), official)
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
        cs.artifact(raw.cached(SOURCE, BOUNDARIES), boundaries)
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
        system = cs.key(f"{SOURCE}.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=alberta, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Alberta referendum")
        election = cs.key(f"{SOURCE}.election", str(DAY))
        cs.add("election", id=election, polity_id=alberta, kind="referendum", date_from=DAY, date_to=DAY, status="held")
        name(election, "2026 Alberta referendum")
        cs.add("slug", path=f"ca/ab/{DAY}-referendum", entity_id=election)
        for question in questions:
            number = question["number"]
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
                source_id=official,
                status="active",
                is_default=True,
                completeness=1,
            )
            rows = results.filter(pl.col("question") == number)
            totals = {"first": rows["first"].sum(), "second": rows["second"].sum()}
            values = [
                {"unit": units[division], "option": None, "measure": 2, "value": electors}
                for division, electors in rows.select("division", "electors").iter_rows()
            ]
            for position, (label, column) in enumerate(zip(question["options"], totals, strict=True), 1):
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
                values.extend(
                    {"unit": units[division], "option": option, "measure": 1, "value": votes}
                    for division, votes in rows.select("division", column).iter_rows()
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
                ("text", question["text"], PAGE, elections),
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
        assembly_elections(cs, alberta, province, name)
        return cs.write()


def assembly_elections(cs: Changeset, alberta: UUID, province: UUID, name: Callable[[object, str], None]) -> None:
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
        cs.artifact(raw.cached(SOURCE, event_page(event)), source)
        cs.artifact(raw.cached(SOURCE, winners_file(event)), source)
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
            cs.artifact(raw.cached(SOURCE, row["url"]), source)
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
