import html
import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.names import compatible, tokens
from ea.store import DATA, connect

SOURCE = "us.council_rosters"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LOG = DATA / "logs" / "council_rosters-unmatched.tsv"
CHRONOLA = "https://cityclerk.lacity.org/chronola/index.cfm?fuseaction=app.OrganizationActive"
NYC = "https://data.cityofnewyork.us/resource/uvw5-9znb.json?$limit=50000"
HOUSTON = "https://www.houstontx.gov/citysec/mayors.pdf"
QUERY = """SELECT ?statement ?person ?label ?start ?startPrecision ?end ?endPrecision ?causeLabel WHERE {{
  VALUES ?position {{ {positions} }}
  ?person p:P39 ?statement . ?statement ps:P39 ?position .
  FILTER NOT EXISTS {{ ?statement wikibase:rank wikibase:DeprecatedRank }}
  OPTIONAL {{ ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }}
  OPTIONAL {{ ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }}
  OPTIONAL {{ ?statement pq:P1534 ?cause . ?cause rdfs:label ?causeLabel FILTER(lang(?causeLabel) = "en") }}
  OPTIONAL {{ ?person rdfs:label ?label FILTER(lang(?label) = "en") }}
}}"""
COUNCILS = {
    "los-angeles": {
        "body": "CA:0644000:council",
        "reader": "chronola",
        "publisher": "City of Los Angeles, Office of the City Clerk",
        "title": "ChronoLA: City of Los Angeles Officials",
        "url": "https://cityclerk.lacity.org/chronola/",
    },
    "new-york-city": {
        "body": "NY:3651000:council",
        "reader": "socrata",
        "publisher": "New York City Council",
        "title": "City Council Members (1999 to Present)",
        "url": "https://data.cityofnewyork.us/City-Government/City-Council-Members-1999-to-Present-/uvw5-9znb",
    },
    "houston": {
        "body": "TX:4835000:council",
        "reader": "houston",
        "publisher": "City of Houston, Office of the City Secretary",
        "title": "List of Mayors, Council Members and City Controllers from 1958",
        "url": HOUSTON,
    },
}
POSITIONS = {
    "los-angeles": ("Q140446581", "Q20162079"),
    "new-york-city": ("Q58620996", "Q131917164"),
}
STARTS = {
    "Elected": "elected",
    "Reelected": "elected",
    "Sworn In": "elected",
    "From": "elected",
    "Special Election": "elected",
    "Reinstated": "elected",
    "Appointed": "appointed",
    "Assumed Office": "appointed",
    "Added": "appointed",
    "Approved": "appointed",
    "Confirmed": "appointed",
    "Reappointed": "appointed",
    "Temporary Appointment": "appointed",
}
ENDS = {
    "Term Expired": "term_end",
    "Completed": "term_end",
    "Through to": "term_end",
    "Resigned": "resigned",
    "Recalled": "removed",
    "Removed": "removed",
    "Removed By Court Order": "removed",
    "Deposed": "removed",
    "Declared Vacant": "removed",
    "Died": "died",
    "Deceased": "died",
    "Retired": "resigned",
    "Elected to US Congress": "resigned",
    "Elected as City Controller": "resigned",
    "Suspended": None,
}
CAUSES = {"death": "died", "resignation": "resigned", "recall": "removed", "recall election": "removed"}
PRECISIONS = {"11": "day", "10": "month", "9": "year"}
FINER = {"unknown": 0, "year": 1, "month": 2, "day": 3}
WINDOW = timedelta(days=400)
CHARTER = date(1925, 7, 1)
CONTINUED = {date(2018, 1, 1)}
CORRECTED = {("7691", "2014-01-01"): date(2016, 2, 23)}
RENAMED = {"Monica Rodriquez": "Monica Rodriguez", "Jack Christie, D.C.": "Jack Christie"}
HOUSTON_SPLITS: dict[
    tuple[int, str], list[tuple[str, str, tuple[date, str] | None, str | None, tuple[date, str] | None]]
] = {
    (1962, "District E"): [
        ("W. H. Jones", "elected", None, "died", (date(1963, 1, 1), "year")),
        ("Frank O. Mancuso", "appointed", (date(1963, 4, 3), "day"), None, None),
    ],
    (1970, "District C"): [
        ("Lee McLemore", "elected", None, "resigned", (date(1971, 12, 21), "day")),
        ("Jim McConn", "appointed", (date(1971, 12, 1), "month"), None, None),
    ],
    (1972, "District C"): [("Jim McConn", "appointed", (date(1972, 1, 2), "day"), None, None)],
    (1982, "At-Large 4"): [
        ("Homer L. Ford", "elected", None, "died", (date(1983, 1, 1), "year")),
        ("Johnny Goyen", "appointed", (date(1983, 2, 1), "day"), None, None),
    ],
    (1988, "District D"): [
        ("Rodney Ellis", "elected", None, None, (date(1989, 12, 1), "month")),
        ("Alfred J. Calloway", "appointed", (date(1989, 12, 28), "day"), None, None),
    ],
    (1990, "District D"): [("Alfred J. Calloway", "appointed", (date(1990, 1, 2), "day"), None, None)],
    (1990, "At-Large 5"): [
        ("Judson Robinson, Jr.", "elected", None, "died", (date(1990, 1, 1), "year")),
        ("Margarette Robinson", "appointed", (date(1990, 12, 19), "day"), None, None),
    ],
    (1992, "District H"): [
        ("Dale M. Gorczynski", "elected", None, None, (date(1993, 1, 1), "month")),
        ("Cynthia Canales Gorczynski", "appointed", (date(1993, 1, 13), "day"), None, None),
    ],
    (1994, "At-Large 4"): [
        ("Sheila Jackson Lee", "elected", None, "resigned", (date(1995, 1, 3), "day")),
        ("John W. Peavy, Jr.", "elected", (date(1995, 2, 1), "month"), None, None),
    ],
    (1996, "At-Large 4"): [
        ("John W. Peavy, Jr.", "elected", None, "resigned", (date(1997, 2, 1), "month")),
        ("Chris Bell", "elected", (date(1997, 2, 1), "month"), None, None),
    ],
    (2006, "At-Large 3"): [
        ("Shelley Sekula-Gibbs", "elected", None, None, (date(2006, 11, 14), "day")),
        ("Melissa Noriega", "elected", (date(2007, 6, 1), "month"), None, None),
    ],
    (2012, "District E"): [
        ("Mike Sullivan", "elected", None, "resigned", (date(2013, 1, 2), "day")),
        ("Dave Martin", "elected", (date(2013, 1, 2), "day"), None, None),
    ],
    (2018, "District K"): [
        ("Larry V. Green", "elected", None, "died", (date(2018, 3, 1), "month")),
        ("Martha Castex-Tatum", "elected", (date(2018, 5, 1), "month"), None, None),
    ],
    (2020, "District B"): [("Tarsha Jackson", "elected", (date(2021, 1, 1), "year"), None, None)],
    (2020, "District G"): [
        ("Greg Travis", "elected", None, None, (date(2022, 2, 2), "day")),
        ("Mary Nan Huffman", "elected", (date(2022, 2, 2), "day"), None, None),
    ],
}
NOTES = {
    "chronola": "Every term of every member of the Los Angeles City Council as the City Clerk's ChronoLA database "
    "lists it, election by election since 1850: the date each member was elected, reelected, appointed, or sworn "
    "in, and the date the term expired or the member resigned, was recalled, or died.",
    "socrata": "Terms of New York City Council members since 1998 as the Council publishes them on NYC Open Data, "
    "with each term's district and start and end dates.",
    "houston": "The City Secretary's list of Houston's mayors, council members, and controllers by term since 1958, "
    "with the appointments, deaths, resignations, and special elections it notes. Terms start and end with the "
    "year printed; dated changes are transcribed from the list's notes.",
    "wikidata": "Council members' terms as recorded in Wikidata (position held, P39), with start and end dates and "
    "end causes from the statement's qualifiers, used where they date a roster term more finely or give its end "
    "cause.",
}


def _sparql(positions: tuple[str, ...]) -> str:
    query = QUERY.format(positions=" ".join(f"wd:{item}" for item in positions))
    return "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})


def _terms() -> list[str]:
    page = raw.cached(SOURCE, CHRONOLA).path.read_text(errors="replace")
    return re.findall(r"<option value='(\d+)'[^>]*>[A-Z][a-z]{2} \d\d, \d{4}", page)


def fetch() -> None:
    raw.fetch(SOURCE, CHRONOLA, suffix=".html")
    for term in _terms():
        key = f"{CHRONOLA}#electionID={term}"
        try:
            raw.cached(SOURCE, key)
        except LookupError:
            response = httpx.post(
                CHRONOLA, data={"electionID": term}, headers={"User-Agent": raw.USER_AGENT}, timeout=300
            )
            response.raise_for_status()
            raw.store(
                SOURCE,
                key,
                response.content,
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", "text/html"),
            )
    raw.fetch(SOURCE, NYC, suffix=".json")
    raw.fetch(SOURCE, HOUSTON, suffix=".pdf")
    for positions in POSITIONS.values():
        raw.fetch(SOURCE, _sparql(positions), suffix=".json")


def _day(text: str) -> date:
    return datetime.strptime(" ".join(text.split()), "%B %d, %Y").date()


def _chronola() -> list[dict[str, object]]:
    rows: dict[tuple[str, str, date], dict[str, object]] = {}
    for term in _terms():
        page = raw.cached(SOURCE, f"{CHRONOLA}#electionID={term}").path.read_text(errors="replace")
        sections = [
            (" ".join(section.split()), body)
            for section, body in re.findall(r"<h4 class='expand[^']*'>([^<]*)</h4>(.*?)(?=<h4 |$)", page, re.DOTALL)
        ]
        districted = any(
            section == "City Council" and re.search(r">\s*Member,? \d+\w\w District", body)
            for section, body in sections
        )
        for section, body in sections:
            if section != "City Council" and (districted or not section.startswith("City Council, ")):
                continue
            office = ""
            for anchor, holder, name, status in re.findall(
                r"OfficeID=\d+\s*>([^<]*)</a>|OfficeHolderID=(\d+)>([^<]*)</a></div><div class='alignright'>([^<]*)</div>",
                body,
            ):
                if anchor:
                    office = " ".join(anchor.split())
                    continue
                match = re.fullmatch(
                    r"\s*([A-Za-z /]+):\s+(.+?)\s+-\s+([A-Za-z /]+):\s+(.+?)\s*", html.unescape(status)
                )
                position = re.fullmatch(r"Member(?:,? (\d+)\w\w (District|Ward))?", office)
                district = re.search(r", (\d+)\w\w (District|Ward)$", section)
                if match is None or position is None or match[3].strip() == "Did not assume office":
                    continue
                number, kind = (
                    (position[1], position[2])
                    if position[1]
                    else (district[1], district[2])
                    if district
                    else (None, None)
                )
                printed = f"{kind} {number}" if number else None
                last, _, given = html.unescape(name).partition(",")
                last = re.sub(r"^(Mr|Mrs|Ms|Miss|Dr)\.?\s+", "", " ".join(last.split()))
                start = _day(match[2])
                seat = printed if start >= CHARTER else None
                rows[(holder, printed or "", start)] = {
                    "council": "los-angeles",
                    "seat": seat,
                    "person_key": f"chronola:{holder}",
                    "name": f"{' '.join(given.split())} {last}".strip(),
                    "start": start,
                    "start_precision": "day",
                    "end": _day(match[4]),
                    "end_precision": "day",
                    "acquired_via": STARTS[match[1].strip()],
                    "ended_via": ENDS[match[3].strip()],
                    "event": "suspended" if match[3].strip() == "Suspended" else None,
                    "resumes": match[1].strip() == "Reinstated",
                    "locator": json.dumps(
                        {"electionID": term, "officeHolderID": holder}
                        | ({"seat": printed} if printed and not seat else {})
                    ),
                }
    return list(rows.values())


def _socrata() -> list[dict[str, object]]:
    rows = []
    records = json.loads(raw.cached(SOURCE, NYC).path.read_text())
    districts: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if "district" in record:
            districts[record["council_member_id"]].add(record["district"])
    for record in records:
        start, end = date.fromisoformat(record["term_start"][:10]), date.fromisoformat(record["term_end"][:10])
        known = districts[record["council_member_id"]]
        district = record.get("district") or (next(iter(known)) if len(known) == 1 else None)
        rows.append(
            {
                "council": "new-york-city",
                "seat": None if district is None else f"District {int(district)}",
                "person_key": f"nyc:{record['council_member_id']}",
                "name": " ".join(record["name"].split()),
                "start": CORRECTED.get((record["council_member_id"], str(start)), start),
                "start_precision": "day",
                "end": end,
                "end_precision": "day",
                "acquired_via": "elected",
                "ended_via": "term_end" if (end.month, end.day) == (12, 31) else None,
                "locator": json.dumps({"council_member_id": record["council_member_id"], "term_start": str(start)}),
            }
        )
    return rows


def _houston() -> tuple[list[dict[str, object]], list[tuple[int, int]]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, HOUSTON).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    terms: list[tuple[int, int, list[tuple[str, str, str]]]] = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if heading := re.fullmatch(r"(\d{4}) ?[–-] ?(\d{4})", line):
            terms.append((int(heading[1]), int(heading[2]), []))
        elif terms and (entry := re.match(r"(.+?) [–-] (District ([A-K])|At Large Position #(\d))\b ?(.*)", line)):
            seat = f"District {entry[3]}" if entry[3] else f"At-Large {entry[4]}"
            terms[-1][2].append((entry[1], seat, line))
    rows: list[dict[str, object]] = []
    sizes: list[tuple[int, int]] = []
    for first, last, entries in terms:
        seats = list(dict.fromkeys(seat for _, seat, _ in entries))
        sizes.append((first, len(seats)))
        current = first >= date.today().year - 4 and last >= date.today().year
        for seat in seats:
            printed = next((name, line) for name, label, line in entries if label == seat)
            holders = HOUSTON_SPLITS.get((first, seat), [(printed[0], "elected", None, None, None)])
            for name, acquired, began, cause, ended in holders:
                start, start_precision = began or (date(first, 1, 1), "year")
                end, end_precision = ended or ((None, "day") if current else (date(last + 1, 1, 1), "year"))
                rows.append(
                    {
                        "council": "houston",
                        "seat": seat,
                        "person_key": None,
                        "name": name,
                        "start": start,
                        "start_precision": start_precision,
                        "end": end,
                        "end_precision": end_precision,
                        "acquired_via": acquired,
                        "ended_via": cause or (None if end is None or ended else "term_end"),
                        "locator": json.dumps({"term": f"{first}–{last}", "line": printed[1]}),
                    }
                )
    merged: list[dict[str, object]] = []
    for row in rows:
        before = next(
            (
                other
                for other in reversed(merged)
                if other["seat"] == row["seat"] and other["name"] == row["name"] and other["end"] == row["start"]
            ),
            None,
        )
        if before is not None and row["start"] in CONTINUED and before["ended_via"] == "term_end":
            before |= {"end": row["end"], "end_precision": row["end_precision"], "ended_via": row["ended_via"]}
        else:
            merged.append(row)
    return merged, sizes


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    houston, sizes = _houston()
    rows = [row | {"name": RENAMED.get(str(row["name"]), row["name"])} for row in [*_chronola(), *_socrata(), *houston]]
    pl.DataFrame(rows, schema_overrides={"start": pl.Date, "end": pl.Date}, infer_schema_length=None).sort(
        "council", "seat", "start", "name"
    ).write_parquet(PARSED / "roster.parquet")
    districted = pl.read_parquet(PARSED / "roster.parquet").filter(
        (pl.col("council") == "los-angeles") & pl.col("seat").str.starts_with("District") & (pl.col("start") >= CHARTER)
    )
    runs: dict[str, list[tuple[str, str, int]]] = {
        "houston": [],
        "los-angeles": [(str(districted["start"].min()), "day", districted["seat"].n_unique())],
    }
    for year, seats in sizes:
        if not runs["houston"] or runs["houston"][-1][2] != seats:
            runs["houston"].append((str(date(year, 1, 1)), "year", seats))
    (PARSED / "sizes.json").write_text(json.dumps(runs))
    statements = []
    for council, positions in POSITIONS.items():
        held: dict[str, dict[str, str]] = {}
        for binding in json.loads(raw.cached(SOURCE, _sparql(positions)).path.read_text())["results"]["bindings"]:
            row = {name: value["value"].rsplit("/", 1)[-1] for name, value in binding.items()}
            if len(row) > len(held.get(row["statement"], {})):
                held[row["statement"]] = row
        for row in held.values():
            statements.append(
                {
                    "council": council,
                    "statement": row["statement"],
                    "person": row["person"],
                    "label": row.get("label", row["person"]),
                    "start": date.fromisoformat(row["start"][:10]) if row.get("start", "")[:1].isdigit() else None,
                    "start_precision": PRECISIONS.get(row.get("startPrecision", ""), "unknown"),
                    "end": date.fromisoformat(row["end"][:10]) if row.get("end", "")[:1].isdigit() else None,
                    "end_precision": PRECISIONS.get(row.get("endPrecision", ""), "unknown"),
                    "cause": CAUSES.get(row.get("causeLabel", "").lower()),
                }
            )
    pl.DataFrame(
        statements, schema_overrides={"start": pl.Date, "end": pl.Date}, infer_schema_length=None
    ).write_parquet(PARSED / "wikidata.parquet")
    frame = pl.read_parquet(PARSED / "roster.parquet")
    print(
        frame.group_by("council")
        .agg(pl.len(), pl.col("start").min().alias("first"), pl.col("start").max().alias("last"))
        .sort("council")
    )
    print(runs)
    print(f"{len(statements)} Wikidata statements")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "roster.parquet")
    statements = pl.read_parquet(PARSED / "wikidata.parquet")
    sizes: dict[str, list[tuple[str, str, int]]] = json.loads((PARSED / "sizes.json").read_text())
    today = date.today()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="City council members' terms from each clerk's official roster (Los Angeles, New York City, "
            "and Houston), with Wikidata where it dates a term more finely, linked to the elections they won",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        for council, spec in COUNCILS.items():
            sources[council] = cs.source(spec["publisher"], spec["title"])
            cs.add(
                "source",
                id=sources[council],
                kind="official_body",
                publisher=spec["publisher"],
                title=spec["title"],
                url=spec["url"],
                notes=NOTES[spec["reader"]],
            )
        wikidata = cs.source("Wikidata", "Members of US city councils")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of US city councils",
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            notes=NOTES["wikidata"],
        )
        for url in [CHRONOLA, *(f"{CHRONOLA}#electionID={term}" for term in _terms())]:
            cs.artifact(raw.cached(SOURCE, url), sources["los-angeles"])
        cs.artifact(raw.cached(SOURCE, NYC), sources["new-york-city"])
        cs.artifact(raw.cached(SOURCE, HOUSTON), sources["houston"])
        for positions in POSITIONS.values():
            cs.artifact(raw.cached(SOURCE, _sparql(positions)), wikidata)
        bodies: dict[str, UUID] = {}
        for council, spec in COUNCILS.items():
            found = conn.execute(
                "select entity_id from external_id where scheme = 'us.body' and value = %s", [spec["body"]]
            ).fetchone()
            assert found is not None, spec["body"]
            bodies[council] = cs.survivors.get(found[0], found[0])
        known: dict[str, UUID] = {
            value: cs.survivors.get(entity, entity)
            for value, entity in conn.execute("select value, entity_id from external_id where scheme = 'wikidata'")
        }
        tagged = set(known.values())
        ran: dict[UUID, dict[UUID, set[str]]] = defaultdict(lambda: defaultdict(set))
        won: dict[UUID, list[tuple[date, UUID, UUID, str | None]]] = defaultdict(list)
        for body, day, contest, person, ballot, preferred, elected, area in conn.execute(
            """select ct.body_id, el.date_from, ct.id, cd.person_id, cd.ballot_name,
                      (select n.text from name n where n.entity_id = cd.person_id and n.is_preferred limit 1),
                      exists (select from outcome oc join result_set r on r.id = oc.result_set_id and r.is_default
                              where oc.option_id = op.id and oc.kind in ('elected', 'won')),
                      (select n.text from name n where n.entity_id = ct.district_area_id and n.is_preferred limit 1)
               from contest ct
               join election el on el.id = ct.election_id
               join contest_option op on op.contest_id = ct.id
               join candidacy cd on cd.option_id = op.id and cd.role = 'head'
               where ct.body_id = any(%s) and el.date_from is not null and ct.party_scope_id is null""",
            [list(bodies.values())],
        ):
            person = cs.survivors.get(person, person)
            ran[body][person] |= {text for text in (ballot, preferred) if text}
            if elected:
                won[body].append((day, contest, person, area))
        unmatched: list[tuple[str, str, str, str]] = []
        resolved: dict[str, UUID] = {}
        named: dict[UUID, str] = {}
        created: set[UUID] = set()
        seated: set[UUID] = set()
        linked: set[UUID] = set()
        tenures: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in frame.iter_rows(named=True):
            council, name, start = row["council"], row["name"], row["start"]
            body = bodies[council]
            parts = tokens(name)
            key = row["person_key"] or f"{council}:{' '.join(parts[0]) if parts else name}"
            person = resolved.get(key)
            if person is None:
                hits = {found for found, texts in ran[body].items() if any(compatible(name, text) for text in texts)}
                recent = {winner for day, _, winner, _ in won[body] if timedelta(0) <= start - day <= WINDOW}
                if len(hits) > 1:
                    hits &= recent
                if not hits and parts is not None:
                    hits = {
                        winner
                        for winner in recent
                        if any(
                            (other := tokens(text)) is not None
                            and other[0][-1] == parts[0][-1]
                            and other[0][0][:1] == parts[0][0][:1]
                            for text in ran[body][winner]
                        )
                    }
                if len(hits) > 1:
                    unmatched.append((council, name, str(start), "ambiguous"))
                    continue
                if not hits and row["person_key"] is None:
                    hits = {
                        found
                        for other, found in resolved.items()
                        if other.startswith(f"{council}:") and compatible(name, named[found])
                    }
                person = next(iter(hits)) if len(hits) == 1 else cs.key(f"{SOURCE}.person", key)
                resolved[key] = person
                named.setdefault(person, name)
            if person in cs.minted and person not in created:
                created.add(person)
                cs.add("person", id=person)
                cs.add(
                    "name",
                    entity_id=person,
                    text=name,
                    lang="en",
                    kind="official",
                    is_preferred=True,
                    source_id=sources[council],
                )
            contest, area = None, None
            if row["acquired_via"] == "elected":
                contest, area = max(
                    (
                        (day, found, label)
                        for day, found, winner, label in won[body]
                        if winner == person and timedelta(0) <= start - day <= WINDOW and found not in linked
                    ),
                    default=(None, None, None),
                    key=lambda entry: entry[0] or date.min,
                )[1:]
            if contest is not None:
                linked.add(contest)
            seat = row["seat"]
            if seat is None and council == "new-york-city" and area and (number := re.search(r"(\d+)\s*$", area)):
                seat = f"District {int(number[1])}"
            seat_id = None
            if seat is not None:
                seat_id = cs.key("us.seat", f"{body}:{seat}")
                if seat_id in cs.minted and seat_id not in seated:
                    seated.add(seat_id)
                    cs.add("seat", id=seat_id, body_id=body, label=seat, voting=True, valid_from=None, valid_to=None)
            future = row["end"] is not None and row["end"] > today
            tenures[council].append(
                row
                | {
                    "key": f"{key}:{row['seat'] or json.loads(row['locator']).get('seat', '')}:{start}",
                    "person": person,
                    "body": body,
                    "seat_id": seat_id,
                    "contest": contest,
                    "end": None if future else row["end"],
                    "ended_via": None if future else row["ended_via"],
                    "citations": [(sources[council], json.loads(row["locator"]))],
                }
            )
        refined = 0
        for statement in statements.iter_rows(named=True):
            entries = [
                entry
                for entry in tenures[statement["council"]]
                if known.get(statement["person"]) == entry["person"]
                or (statement["person"] not in known and compatible(statement["label"], str(entry["name"])))
            ]
            if not entries or len({entry["person"] for entry in entries}) > 1:
                continue
            person = entries[0]["person"]
            if statement["person"] not in known and person not in tagged:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=statement["person"])
                known[statement["person"]] = person
                tagged.add(person)
            used = False
            if statement["start"] is not None:
                entry = min(entries, key=lambda entry: abs(entry["start"] - statement["start"]))
                if (
                    entry["start"].year == statement["start"].year
                    and FINER[statement["start_precision"]] > FINER[entry["start_precision"]]
                ):
                    entry["start"], entry["start_precision"] = statement["start"], statement["start_precision"]
                    entry["citations"].append((wikidata, {"statement": statement["statement"]}))
                    used = True
            ended = [entry for entry in entries if entry["end"] is not None]
            if statement["end"] is not None and ended:
                entry = min(ended, key=lambda entry: abs(entry["end"] - statement["end"]))
                if abs(entry["end"] - statement["end"]) <= timedelta(days=366):
                    changed = False
                    if FINER[statement["end_precision"]] > FINER[entry["end_precision"]]:
                        entry["end"], entry["end_precision"] = statement["end"], statement["end_precision"]
                        changed = True
                    if entry["ended_via"] in (None, "term_end") and statement["cause"] is not None:
                        entry["ended_via"] = statement["cause"]
                        changed = True
                    if changed:
                        entry["citations"].append((wikidata, {"statement": statement["statement"]}))
                        used = True
            refined += used
        overlaps = 0
        for entries in tenures.values():
            held: dict[UUID, dict[str, Any]] = {}
            for entry in sorted(entries, key=lambda entry: (entry["start"], entry["end"] or date.max)):
                before = held.get(entry["seat_id"]) if entry["seat_id"] else None
                if before is not None and before["end"] is not None and entry["start"] < before["end"]:
                    if before["person"] == entry["person"]:
                        if entry["end"] is not None and entry["end"] <= before["end"]:
                            entry["drop"] = True
                            continue
                        entry["start"], entry["start_precision"] = before["end"] + timedelta(days=1), "day"
                    else:
                        before["end"], before["end_precision"] = entry["start"], entry["start_precision"]
                        if before["ended_via"] == "term_end":
                            before["ended_via"] = None
                    overlaps += 1
                if entry["seat_id"]:
                    held[entry["seat_id"]] = entry
        count = 0
        for council, entries in tenures.items():
            previous: dict[UUID, UUID] = {}
            removed: dict[UUID, UUID] = {}
            for entry in sorted(entries, key=lambda entry: (entry["start"], str(entry["name"]))):
                if entry.get("drop"):
                    continue
                tenure = cs.key(f"{SOURCE}.tenure", entry["key"])
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=entry["person"],
                    body_id=entry["body"],
                    seat_id=entry["seat_id"],
                    from_date=entry["start"],
                    from_precision=entry["start_precision"],
                    to_date=entry["end"],
                    to_precision=entry["end_precision"],
                    acquired_via=entry["acquired_via"],
                    contest_id=entry["contest"],
                    ended_via=entry["ended_via"],
                    resumes_tenure_id=removed.get(entry["person"]) if entry["resumes"] else None,
                    succeeds_tenure_id=previous.get(entry["seat_id"]) if entry["seat_id"] else None,
                )
                if entry["event"] == "suspended" and entry["end"] is not None:
                    cs.add(
                        "tenure_event",
                        tenure_id=tenure,
                        kind="suspended",
                        date=entry["end"],
                        date_precision=entry["end_precision"],
                        source_id=sources[council],
                    )
                for source, locator in entry["citations"]:
                    cs.add("citation", entity_id=tenure, source_id=source, locator=locator)
                if entry["seat_id"]:
                    previous[entry["seat_id"]] = tenure
                if entry["ended_via"] == "removed":
                    removed[entry["person"]] = tenure
                count += 1
        for council, runs in sizes.items():
            for index, (since, precision, seats) in enumerate(runs):
                cs.add(
                    "body_size",
                    body_id=bodies[council],
                    seats=seats,
                    valid_from=date.fromisoformat(since),
                    valid_from_precision=precision,
                    valid_to=date.fromisoformat(runs[index + 1][0]) if index + 1 < len(runs) else None,
                    valid_to_precision=runs[index + 1][1] if index + 1 < len(runs) else "day",
                )
        LOG.write_text("council\tname\tstart\treason\n" + "".join("\t".join(row) + "\n" for row in sorted(unmatched)))
        print(
            f"{count} tenures, {len(created)} new people, {len(linked)} won contests linked, {overlaps} seat overlaps settled, "
            f"{refined} Wikidata statements used; {len(unmatched)} terms unmatched, logged to {LOG}"
        )
        return cs.write()
