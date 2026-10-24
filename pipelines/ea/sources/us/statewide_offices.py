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
from ea.sources.us.governors import API, DATE, MONTHS, PAGES, PRECISIONS, SUFFIXES, WIKIPEDIA
from ea.store import DATA, connect

SOURCE = "us.statewide_offices"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WINDOW = timedelta(days=150)
INCLUDE = (
    "Lieutenant Governor|Attorney General|Secretary of State|Secretary of the Commonwealth|Treasurer|Auditor|"
    "Comptroller|Controller|Superintendent|Commissioner|Surveyor General|Mine Inspector|Adjutant General|"
    "Secretary of Agriculture|Secretary of Internal Affairs|State Engineer|State Printer|Public Instruction|"
    "Secretary of the Territory|Register of the Land Office|Corporation Commission"
)
EXCLUDE = (
    "Deputy|Assistant|Solicitor|Speaker|Leader|Whip|Pro Tempore|First Lady|Justice|Judge|Senator|Representative|"
    "member of|Member of|Office of|Department|Board of|Council|Chief of Staff|County|City|Mayor|Court of|Senate|House|"
    "Delegate|Police|Fire|Bureau|Division|Agency|Authority|Cabinet|Inspector General|Public Service Commission|"
    "Bishop|Lord|Governor-General|Governor of the Territory|Regent|Surrogate|Chancellor"
)
QUERY = """SELECT ?pos ?posLabel ?article ?statement ?person ?label ?start ?startPrecision ?end ?endPrecision ?death WHERE {{
  ?state wdt:P300 "US-{state}" .
  ?pos wdt:P1001 ?state ; rdfs:label ?posLabel .
  FILTER(lang(?posLabel) = "en" && REGEX(?posLabel, "{include}") && !REGEX(?posLabel, "{exclude}"))
  ?person wdt:P31 wd:Q5 ; p:P39 ?statement . ?statement ps:P39 ?pos .
  FILTER NOT EXISTS {{ ?statement wikibase:rank wikibase:DeprecatedRank }}
  OPTIONAL {{ ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }}
  OPTIONAL {{ ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }}
  OPTIONAL {{ ?person wdt:P570 ?death }}
  OPTIONAL {{ ?person rdfs:label ?label FILTER(lang(?label) = "en") }}
  OPTIONAL {{ ?article schema:about ?pos ; schema:isPartOf <https://en.wikipedia.org/> }}
}}"""
CLASSES = (
    ("lieutenant governor", "lieutenant_governor"),
    ("attorney general", "attorney_general"),
    ("secretary of state|secretary of the commonwealth|secretary of the territory", "secretary_of_state"),
    ("treasurer", "treasurer"),
    ("auditor|comptroller|controller", "auditor"),
    ("superintendent|public instruction", "superintendent"),
)
NAME = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
SKIP = re.compile(r"Party|^\d|election|Republican|Democrat|Whig|Federalist|Independent|^List |^File:|^Image:|Union|Medal|Award|Merit|Order of|Seal|Constitution|Legislature|University|College|^State of|Governor of|Lieutenant Governor of")
YEARS = re.compile(r"(\d{4})\s*(?:[–—-]|&ndash;|&mdash;|\bto\b)\s*(\d{4}|present|incumbent)?", re.IGNORECASE)
NOTES = (
    "Terms of statewide executive offices (lieutenant governor, attorney general, secretary of state, treasurer, "
    "auditor or comptroller, superintendent, and the other offices states elect) as recorded in Wikidata (position "
    "held, P39) for positions that apply to a state, with start and end dates."
)
WIKIPEDIA_NOTES = (
    "Start and end dates for officeholders whose Wikidata terms have no dates, read from the tables of holders in "
    "each office's English Wikipedia article."
)


def _query(state: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {
            "query": QUERY.format(state=state, include=INCLUDE, exclude=EXCLUDE),
            "format": "json",
        }
    )


def _article(title: str) -> str:
    return WIKIPEDIA + urlencode({"title": title, "action": "raw"})


def _humans(qids: list[str]) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {
            "query": "SELECT ?p WHERE { VALUES ?p { " + " ".join(f"wd:{qid}" for qid in qids) + " } ?p wdt:P31 wd:Q5 }",
            "format": "json",
        }
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


def _bindings(state: str) -> list[dict[str, str]]:
    return [
        {
            name: value["value"] if name == "article" else value["value"].rsplit("/", 1)[-1]
            for name, value in b.items()
            if value["type"] != "uri" or "/.well-known/genid/" not in value["value"]
        }
        for b in json.loads(raw.cached(SOURCE, _query(state)).path.read_text())["results"]["bindings"]
    ]


def _titles(state: str) -> dict[str, str]:
    return {row["pos"]: row["article"].split("/wiki/", 1)[1] for row in _bindings(state) if "article" in row}


def _links(title: str) -> list[str]:
    return sorted({found[1].strip() for found in NAME.finditer(raw.cached(SOURCE, _article(title)).path.read_text())})


def fetch() -> None:
    for state in PAGES:
        raw.fetch(SOURCE, _query(state), suffix=".json")
        for title in sorted(set(_titles(state).values())):
            raw.fetch(SOURCE, _article(title), suffix=".wiki")
            links = [link for link in _links(title) if not SKIP.search(link)]
            for index in range(0, len(links), 50):
                raw.fetch(SOURCE, _lookup(links[index : index + 50]), suffix=".json")


def parse() -> None:
    held: dict[str, dict[str, str]] = {}
    positions: dict[tuple[str, str], str] = {}
    for state in PAGES:
        for row in _bindings(state):
            if "start" in row and row["start"].startswith("-") or "end" in row and row["end"].startswith("-"):
                continue
            positions[(state, row["pos"])] = row["posLabel"]
            row["state"] = state
            if len(row) > len(held.get(row["statement"], {})):
                held[row["statement"]] = row
    listed: dict[tuple[str, str, str], list[tuple[str, date, date | None, str]]] = defaultdict(list)
    for state in PAGES:
        for pos, title in _titles(state).items():
            links = [link for link in _links(title) if not SKIP.search(link)]
            items: dict[str, str] = {}
            for index in range(0, len(links), 50):
                answer = json.loads(raw.cached(SOURCE, _lookup(links[index : index + 50])).path.read_text())["query"]
                for page in answer.get("pages", {}).values():
                    if "wikibase_item" in page.get("pageprops", {}):
                        items[page["title"]] = page["pageprops"]["wikibase_item"]
                for hop in ("redirects", "normalized"):
                    for step in answer.get(hop, []):
                        items[step["from"]] = step["to"]
            text = re.sub(
                r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->",
                "",
                raw.cached(SOURCE, _article(title)).path.read_text(),
                flags=re.DOTALL,
            )
            while (
                stripped := re.sub(
                    r"\{\{([^{}]*)\}\}",
                    lambda found: (
                        found[1].split("|", 1)[1]
                        if "|" in found[1]
                        and found[1].split("|")[0].strip().lower() in {"small", "nowrap", "nobr", "sort", "dts"}
                        else ""
                    ),
                    text,
                )
            ) != text:
                text = stripped
            for chunk in text.split("\n|-"):
                cells = chunk.split("\n|")
                found = next((m for m in NAME.finditer(chunk) if not SKIP.search(m[1]) and m[1].strip() in items), None)
                if found is None:
                    continue
                title_ = linked = found[1].strip()
                while title_ in items and not re.fullmatch(r"Q\d+", title_):
                    title_ = items[title_]
                if not re.fullmatch(r"Q\d+", title_):
                    continue
                dates = [
                    date(int(year), MONTHS.split("|").index(month) + 1, int(day))
                    for month, day, year in DATE.findall(chunk[found.end() :])
                ]
                if dates:
                    listed[(state, pos, title_)].append(
                        (linked, dates[0], dates[1] if len(dates) > 1 and dates[1] > dates[0] else None, "day")
                    )
                    continue
                years = YEARS.search(" ".join(cells[1:]) if len(cells) > 1 else chunk[found.end() :])
                if years is not None and 1780 <= int(years[1]) <= date.today().year:
                    end = int(years[2]) if years[2] and years[2].isdigit() else None
                    listed[(state, pos, title_)].append(
                        (
                            linked,
                            date(int(years[1]), 1, 1),
                            date(end, 1, 1) if end and end > int(years[1]) else None,
                            "year",
                        )
                    )
    candidates = sorted({qid for _, _, qid in listed})
    humans = {
        binding["p"]["value"].rsplit("/", 1)[-1]
        for index in range(0, len(candidates), 200)
        for binding in json.loads(
            raw.fetch(SOURCE, _humans(candidates[index : index + 200]), suffix=".json").path.read_text()
        )["results"]["bindings"]
    }
    listed = defaultdict(list, {key: terms for key, terms in listed.items() if key[2] in humans})
    rows: list[dict[str, object]] = []
    people: dict[tuple[str, str], dict[str, object]] = {}
    for row in held.values():
        base = {
            "state": row["state"],
            "position": row["pos"],
            "office": positions[(row["state"], row["pos"])],
            "person": row["person"],
            "label": row.get("label", row["person"]),
            "death": date.fromisoformat(row["death"][:10]) if row.get("death", "")[:1].isdigit() else None,
        }
        people[(row["state"], row["person"])] = base
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
    for (state, pos, qid), terms in listed.items():
        for label, start, end, precision in terms:
            rows.append(
                {
                    **people.get(
                        (state, qid),
                        {
                            "state": state,
                            "position": pos,
                            "office": positions[(state, pos)],
                            "person": qid,
                            "label": label,
                            "death": None,
                        },
                    ),
                    "position": pos,
                    "office": positions[(state, pos)],
                    "statement": f"{state}:{pos}:{qid}:{start}",
                    "origin": "wikipedia",
                    "start": start,
                    "start_precision": precision,
                    "end": end,
                    "end_precision": precision,
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"start": pl.Date, "end": pl.Date, "death": pl.Date}).unique(
        "statement", keep="first"
    )
    dated = frame.filter(pl.col("origin") == "wikidata").select(
        "state", "position", "person", began="start", ended="end"
    )
    clashing = (
        frame.filter(pl.col("origin") == "wikipedia")
        .join(dated, on=["state", "position", "person"])
        .filter(
            pl.col("start") >= pl.col("began") - pl.duration(days=366),
            pl.col("start") < pl.col("ended").fill_null(date.today()) + pl.duration(days=366),
        )["statement"]
    )
    frame.filter(~pl.col("statement").is_in(clashing.implode())).sort("state", "position", "start").select(
        "state",
        "position",
        "office",
        "statement",
        "person",
        "label",
        "start",
        "start_precision",
        "end",
        "end_precision",
        "death",
        "origin",
    ).write_parquet(PARSED / "tenures.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "tenures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide executive officeholders and their terms from Wikidata and Wikipedia, linked to the elections they won",
            polity="US",
        )
        wikidata = cs.source("Wikidata", "Statewide executive officeholders of US states and territories")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Statewide executive officeholders of US states and territories",
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            notes=NOTES,
        )
        wikipedia = cs.source("Wikipedia", "Lists of officeholders of US statewide executive offices")
        cs.add(
            "source",
            id=wikipedia,
            kind="community",
            publisher="Wikipedia",
            title="Lists of officeholders of US statewide executive offices",
            url="https://en.wikipedia.org/wiki/State_executive_official_(United_States)",
            license="CC BY-SA 4.0",
            notes=WIKIPEDIA_NOTES,
        )
        for state in PAGES:
            cs.artifact(raw.cached(SOURCE, _query(state)), wikidata)
            for title in sorted(set(_titles(state).values())):
                cs.artifact(raw.cached(SOURCE, _article(title)), wikipedia)
                links = [link for link in _links(title) if not SKIP.search(link)]
                for index in range(0, len(links), 50):
                    cs.artifact(raw.cached(SOURCE, _lookup(links[index : index + 50])), wikipedia)
        existing: dict[str, dict[str, UUID]] = defaultdict(dict)
        for value, entity in conn.execute(
            "select x.value, x.entity_id from external_id x join office o on o.id = x.entity_id"
            " where x.scheme = 'us.office' and o.kind <> 'governor'"
        ).fetchall():
            key, _, state = value.rpartition(":")
            existing[state][key] = entity
        known: dict[str, UUID] = dict(
            conn.execute(
                "select x.value, x.entity_id from external_id x join person p on p.id = x.entity_id"
                " where x.scheme = 'wikidata'"
            ).fetchall()
        )
        other = {
            value
            for (value,) in conn.execute(
                "select x.value from external_id x where x.scheme = 'wikidata'"
                " and not exists (select from person p where p.id = x.entity_id)"
            )
        }
        tagged = set(known.values())
        won: dict[UUID, list[tuple[date, UUID, UUID, set[str]]]] = defaultdict(list)
        for office, day, contest, person, ballot in conn.execute(
            """select ct.office_id,
                      case when el.date_precision = 'year' then make_date(extract(year from el.date_from)::int, 11, 1)
                      else el.date_from end,
                      ct.id, cd.person_id, cd.ballot_name
               from contest ct
               join office o on o.id = ct.office_id and o.kind <> 'governor'
               join election el on el.id = ct.election_id
               join contest_option op on op.contest_id = ct.id
               join candidacy cd on cd.option_id = op.id and cd.role = 'head'
               where (el.kind <> 'primary' and ct.party_scope_id is null) and cd.person_id is not null"""
        ).fetchall():
            won[office].append((day, contest, person, set(slug(ballot or "").split("-"))))
        offices: dict[tuple[str, str], UUID] = {}
        persons: dict[str, UUID] = {}
        linked: set[UUID] = set()
        previous: dict[tuple[str, str, str], UUID] = {}
        for (
            state,
            position,
            office_label,
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
            if qid in other:
                continue
            if (state, position) not in offices:
                stem = re.sub(
                    r"^(?:the|of)\s+|\s+(?:of|the)$",
                    "",
                    re.sub(
                        r"\s+", " ", re.sub(r"(?<!of )\bState\b", "", office_label.replace(PAGES[state], ""))
                    ).strip(),
                )
                candidate = slug(stem)
                tokens = set(candidate.split("-")) - {"of", "the", "and"}
                found = existing[state].get(candidate)
                if found is None:
                    found = next(
                        (
                            entity
                            for key, entity in existing[state].items()
                            if (other := set(key.split("-")) - {"of", "the", "and"}) == tokens
                            or (len(tokens & other) >= 2 and (tokens <= other or other <= tokens))
                        ),
                        None,
                    )
                if found is None:
                    found = cs.key("us.office", f"{candidate}:{state}")
                    cs.add("office", id=found, polity_id=cs.polity(f"US-{state}"), kind="other")
                    cs.add("name", entity_id=found, text=office_label, lang="en", kind="official", is_preferred=True)
                    existing[state][candidate] = found
                offices[(state, position)] = found
            office = offices[(state, position)]
            words = [word for word in slug(label).split("-") if word not in SUFFIXES]
            surname = words[-1] if words else ""
            match = next(
                (
                    (contest, person)
                    for day, contest, person, ballot in won[office]
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
            scheduled = start is not None and any(timedelta(0) <= start - day <= WINDOW for day, *_ in won[office])
            tenure = cs.key(f"{SOURCE}.tenure", statement)
            cs.add(
                "tenure",
                id=tenure,
                person_id=persons[qid],
                office_id=office,
                from_date=start,
                from_precision=start_precision,
                to_date=end,
                to_precision=end_precision,
                acquired_via="elected" if contest is not None or scheduled else "succession",
                contest_id=contest,
                ended_via="died"
                if end is not None and death is not None and abs(death - end) <= timedelta(days=1)
                else None,
                succeeds_tenure_id=previous.get((state, position, "last")),
            )
            cs.add(
                "citation",
                entity_id=tenure,
                source_id=wikidata if origin == "wikidata" else wikipedia,
                locator={"statement": statement} if origin == "wikidata" else {"page": office_label},
            )
            previous[(state, position, "last")] = tenure
        humans = {str(qid) for qid in frame["person"].unique()}
        stale = [
            (person, tenure)
            for person, qid, tenure in conn.execute(
                """select p.id, x.value, t.id from person p
                   join external_id x on x.entity_id = p.id and x.scheme = 'wikidata'
                   join entity en on en.id = p.id and en.type = 'person'
                   join changeset c on c.id = p.changeset_id and c.pipeline = %s
                   left join tenure t on t.person_id = p.id and t.changeset_id = p.changeset_id""",
                [SOURCE],
            ).fetchall()
            if qid not in humans
        ]
        retired = [tenure for _, tenure in stale if tenure is not None] or sorted({person for person, _ in stale})
        if stale and any(tenure is not None for _, tenure in stale):
            cursor = conn.execute(
                "select id, person_id, office_id, from_date, from_precision, to_date, to_precision, acquired_via,"
                " contest_id, ended_via from tenure where id = any(%s)",
                [retired],
            )
            for row in cursor.fetchall():
                cs.add("tenure", **dict(zip([d.name for d in cursor.description], row, strict=True)), succeeds_tenure_id=None)
        for entity in retired:
            cs.add("entity_retirement", entity_id=entity, reason="Linked from an officeholder table but not a human in Wikidata")
        return cs.write()
