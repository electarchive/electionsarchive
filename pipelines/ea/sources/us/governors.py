import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
QUERY = """SELECT ?iso ?office ?statement ?person ?label ?start ?startPrecision ?end ?endPrecision ?death WHERE {
  VALUES ?kind { wd:Q35657 wd:Q1352230 wd:Q783733 }
  ?state wdt:P31 ?kind ; wdt:P300 ?iso ; wdt:P1313 ?office .
  FILTER(STRSTARTS(?iso, "US-"))
  ?person p:P39 ?statement . ?statement ps:P39 ?office .
  FILTER NOT EXISTS { ?statement wikibase:rank wikibase:DeprecatedRank }
  OPTIONAL { ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }
  OPTIONAL { ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }
  OPTIONAL { ?person wdt:P570 ?death }
  OPTIONAL { ?person rdfs:label ?label FILTER(lang(?label) = "en") }
}"""
GOVERNORS = "https://query.wikidata.org/sparql?" + urlencode({"query": QUERY, "format": "json"})
PRECISIONS = {"11": "day", "10": "month", "9": "year"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
WINDOW = timedelta(days=150)
PAGES = {
    "AK": "Alaska",
    "AL": "Alabama",
    "AR": "Arkansas",
    "AS": "American Samoa",
    "AZ": "Arizona",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "GU": "Guam",
    "HI": "Hawaii",
    "IA": "Iowa",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "MA": "Massachusetts",
    "MD": "Maryland",
    "ME": "Maine",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MO": "Missouri",
    "MP": "the Northern Mariana Islands",
    "MS": "Mississippi",
    "MT": "Montana",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "NE": "Nebraska",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NV": "Nevada",
    "NY": "New York",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "PR": "Puerto Rico",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VA": "Virginia",
    "VT": "Vermont",
    "WA": "Washington",
    "WI": "Wisconsin",
    "WV": "West Virginia",
    "WY": "Wyoming",
}
WIKIPEDIA = "https://en.wikipedia.org/w/index.php?"
API = "https://en.wikipedia.org/w/api.php?"
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b({MONTHS}) (\d{{1,2}}), (\d{{4}})")
NAME = re.compile(r"'''\[\[([^\]|#]+)|\[\[([^\]|#]+)\|'''")
KEPT = {"small", "nowrap", "nobr", "sort", "dts"}
WIKIPEDIA_NOTES = (
    "Start and end dates for the governors whose Wikidata terms have no dates, read from each state's list of governors "
    "on the English Wikipedia."
)
NOTES = (
    "Governor terms as recorded in Wikidata (position held, P39, of the office each state names as its head of "
    "government), including start and end dates. Governors who served consecutive terms usually have one entry."
)


def _page(state: str) -> str:
    return WIKIPEDIA + urlencode({"title": f"List of governors of {PAGES[state]}", "action": "raw"})


def _titles(state: str) -> list[str]:
    return sorted(
        {(found[1] or found[2]).strip() for found in NAME.finditer(raw.cached(SOURCE, _page(state)).path.read_text())}
    )


def _lookup(titles: list[str]) -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "redirects": 1,
            "format": "json",
            "titles": "|".join(titles),
        }
    )


def fetch() -> None:
    raw.fetch(SOURCE, GOVERNORS, suffix=".json")
    for state in PAGES:
        raw.fetch(SOURCE, _page(state), suffix=".wiki")
        titles = _titles(state)
        for index in range(0, len(titles), 50):
            raw.fetch(SOURCE, _lookup(titles[index : index + 50]), suffix=".json")


def parse() -> None:
    held: dict[str, dict[str, str]] = {}
    for binding in json.loads(raw.cached(SOURCE, GOVERNORS).path.read_text())["results"]["bindings"]:
        row = {
            name: value["value"].rsplit("/", 1)[-1]
            for name, value in binding.items()
            if value["type"] != "uri" or "/.well-known/genid/" not in value["value"]
        }
        if len(row) > len(held.get(row["statement"], {})):
            held[row["statement"]] = row
    listed: dict[tuple[str, str], list[tuple[str, date, date | None]]] = defaultdict(list)
    for state in PAGES:
        titles = _titles(state)
        items: dict[str, str] = {}
        for index in range(0, len(titles), 50):
            answer = json.loads(raw.cached(SOURCE, _lookup(titles[index : index + 50])).path.read_text())["query"]
            for page in answer.get("pages", {}).values():
                if "wikibase_item" in page.get("pageprops", {}):
                    items[page["title"]] = page["pageprops"]["wikibase_item"]
            for hop in ("redirects", "normalized"):
                for step in answer.get(hop, []):
                    items[step["from"]] = step["to"]
        text = re.sub(
            r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->",
            "",
            raw.cached(SOURCE, _page(state)).path.read_text(),
            flags=re.DOTALL,
        )
        while True:
            stripped = re.sub(
                r"\{\{([^{}]*)\}\}",
                lambda found: (
                    found[1].split("|", 1)[1]
                    if "|" in found[1] and found[1].split("|")[0].strip().lower() in KEPT
                    else ""
                ),
                text,
            )
            if stripped == text:
                break
            text = stripped
        for chunk in text.split("\n|-"):
            found = NAME.search(chunk)
            if found is None or re.search(r"^\d|election|Party", found[1] or found[2]):
                continue
            title = linked = (found[1] or found[2]).strip()
            while title in items and not re.fullmatch(r"Q\d+", title):
                title = items[title]
            for cell in chunk[found.end() :].split("\n|"):
                dates = [
                    date(int(year), MONTHS.split("|").index(month) + 1, int(day))
                    for month, day, year in DATE.findall(cell)
                ]
                if dates:
                    if re.fullmatch(r"Q\d+", title):
                        listed[(state, title)].append(
                            (
                                re.sub(r" \([^)]*\)$", "", linked),
                                dates[0],
                                dates[1] if len(dates) > 1 and dates[1] > dates[0] else None,
                            )
                        )
                    break
    rows: list[dict[str, object]] = []
    people: dict[tuple[str, str], dict[str, str | date | None]] = {}
    for row in held.values():
        if (
            row.get("start", "").startswith("-")
            or row.get("end", "").startswith("-")
            or row.get("label", "Governor of").startswith("Governor of")
        ):
            continue
        state = row["iso"].removeprefix("US-")
        base = {
            "state": state,
            "person": row["person"],
            "label": row.get("label", row["person"]),
            "death": date.fromisoformat(row["death"][:10]) if row.get("death", "")[:1].isdigit() else None,
        }
        people[(state, row["person"])] = base
        if "start" in row:
            rows.append(
                {
                    **base,
                    "statement": row["statement"],
                    "origin": "wikidata",
                    "start": date.fromisoformat(row["start"][:10]),
                    "start_precision": PRECISIONS.get(row.get("startPrecision", ""), "unknown"),
                    "end": date.fromisoformat(row["end"][:10]) if "end" in row else None,
                    "end_precision": PRECISIONS.get(row.get("endPrecision", ""), "day"),
                }
            )
    for (state, qid), terms in listed.items():
        for label, start, end in terms:
            rows.append(
                {
                    **people.get((state, qid), {"state": state, "person": qid, "label": label, "death": None}),
                    "statement": f"{state}:{qid}:{start}",
                    "origin": "wikipedia",
                    "start": start,
                    "start_precision": "day",
                    "end": end,
                    "end_precision": "day",
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"start": pl.Date, "end": pl.Date, "death": pl.Date}).unique(
        "statement", keep="first"
    )
    dated = frame.filter(pl.col("origin") == "wikidata").select("state", "person", began="start", ended="end")
    clashing = (
        frame.filter(pl.col("origin") == "wikipedia")
        .join(dated, on=["state", "person"])
        .filter(
            pl.col("start") >= pl.col("began") - pl.duration(days=30),
            pl.col("start") < pl.col("ended").fill_null(date.today()) - pl.duration(days=30),
        )["statement"]
    )
    frame.filter(~pl.col("statement").is_in(clashing.implode())).sort("state", "start").select(
        "state", "statement", "person", "label", "start", "start_precision", "end", "end_precision", "death", "origin"
    ).write_parquet(PARSED / "tenures.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "tenures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governors' tenures in every state and territory from Wikidata, linked to the elections they won",
            polity="US",
        )
        wikidata = cs.source("Wikidata", "Governors of US states and territories")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Governors of US states and territories",
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            notes=NOTES,
        )
        cs.artifact(raw.cached(SOURCE, GOVERNORS), wikidata)
        wikipedia = cs.source("Wikipedia", "Lists of governors of US states and territories")
        cs.add(
            "source",
            id=wikipedia,
            kind="community",
            publisher="Wikipedia",
            title="Lists of governors of US states and territories",
            url="https://en.wikipedia.org/wiki/Lists_of_United_States_governors",
            license="CC BY-SA 4.0",
            notes=WIKIPEDIA_NOTES,
        )
        for state in PAGES:
            cs.artifact(raw.cached(SOURCE, _page(state)), wikipedia)
            titles = _titles(state)
            for index in range(0, len(titles), 50):
                cs.artifact(raw.cached(SOURCE, _lookup(titles[index : index + 50])), wikipedia)
        offices: dict[str, UUID] = {
            value.removeprefix("governor:"): entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'us.office' and value like 'governor:%%'"
            ).fetchall()
        }
        known: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'wikidata'").fetchall()
        )
        tagged = set(known.values())
        won: dict[str, list[tuple[date, UUID, UUID, set[str]]]] = defaultdict(list)
        calendar: dict[str, list[date]] = defaultdict(list)
        for value, day, contest, person, ballot in conn.execute(
            """select x.value,
                      case when el.date_precision = 'year' then make_date(extract(year from el.date_from)::int, 11, 1)
                      else el.date_from end,
                      ct.id, cd.person_id, cd.ballot_name
               from contest ct
               join external_id x on x.entity_id = ct.office_id and x.scheme = 'us.office' and x.value like 'governor:%%'
               join election el on el.id = ct.election_id
               join contest_option op on op.contest_id = ct.id
               join candidacy cd on cd.option_id = op.id and cd.role = 'head'
               where el.kind <> 'primary' or ct.party_scope_id is null"""
        ).fetchall():
            state = value.removeprefix("governor:")
            won[state].append((day, contest, person, set(slug(ballot or "").split("-"))))
            calendar[state].append(day)
        persons: dict[str, UUID] = {}
        linked: set[UUID] = set()
        previous: dict[str, UUID] = {}
        for (
            state,
            statement,
            qid,
            label,
            start,
            start_precision,
            end,
            end_precision,
            death,
            origin,
        ) in frame.iter_rows():
            if state not in offices:
                continue
            words = [word for word in slug(label).split("-") if word not in SUFFIXES]
            surname = words[-1] if words else ""
            match = next(
                (
                    (contest, person)
                    for day, contest, person, ballot in won[state]
                    if start is not None and timedelta(0) <= start - day <= WINDOW and surname in ballot
                ),
                None,
            )
            if qid not in persons:
                if qid in known:
                    persons[qid] = known[qid]
                elif match is not None and match[1] not in tagged:
                    persons[qid] = match[1]
                    tagged.add(match[1])
                    cs.add("external_id", entity_id=match[1], scheme="wikidata", value=qid)
                else:
                    persons[qid] = cs.key("wikidata", qid)
                    cs.add("person", id=persons[qid])
                    cs.add("name", entity_id=persons[qid], text=label, lang="en", kind="official", is_preferred=True)
            contest = match[0] if match is not None and match[1] == persons[qid] and match[0] not in linked else None
            if contest is not None:
                linked.add(contest)
            scheduled = start is not None and any(timedelta(0) <= start - day <= WINDOW for day in calendar[state])
            covered = start is not None and any(abs(start - day) <= timedelta(days=5 * 366) for day in calendar[state])
            tenure = cs.key(f"{SOURCE}.tenure", statement)
            cs.add(
                "tenure",
                id=tenure,
                person_id=persons[qid],
                office_id=offices[state],
                from_date=start,
                from_precision=start_precision,
                to_date=end,
                to_precision=end_precision,
                acquired_via="elected" if contest is not None or scheduled or not covered else "succession",
                contest_id=contest,
                ended_via="died"
                if end is not None and death is not None and abs(death - end) <= timedelta(days=1)
                else None,
                succeeds_tenure_id=previous.get(state),
            )
            cs.add(
                "citation",
                entity_id=tenure,
                source_id=wikidata if origin == "wikidata" else wikipedia,
                locator={"statement": statement}
                if origin == "wikidata"
                else {"page": f"List of governors of {PAGES[state]}"},
            )
            previous[state] = tenure
        return cs.write()
