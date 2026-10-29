import io
import json
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.names import compatible
from ea.sources.us import alged, census
from ea.store import DATA, connect

SOURCE = "us.wikidata_mayors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LOG = DATA / "logs" / "wikidata_mayors-unmatched.tsv"
LOOKUP = "SELECT ?city ?value WHERE {{ VALUES ?value {{ {values} }} ?city wdt:{code} ?value }}"
QUERY = """SELECT ?city ?statement ?person ?label ?start ?startPrecision ?end ?endPrecision ?death ?roleLabel WHERE {{
  VALUES ?city {{ {items} }}
  ?office wdt:P1001 ?city .
  {{ ?city wdt:P1313 ?office }} UNION {{ ?office wdt:P279 wd:Q30185 }}
  ?person p:P39 ?statement . ?statement ps:P39 ?office .
  FILTER NOT EXISTS {{ ?statement wikibase:rank wikibase:DeprecatedRank }}
  OPTIONAL {{ ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }}
  OPTIONAL {{ ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }}
  OPTIONAL {{ ?statement pq:P2868|pq:P5102 ?role . ?role rdfs:label ?roleLabel FILTER(lang(?roleLabel) = "en") }}
  OPTIONAL {{ ?person wdt:P570 ?death }}
  OPTIONAL {{ ?person rdfs:label ?label FILTER(lang(?label) = "en") }}
}}"""
CHUNK = 200
ITEMS = {"place:4752006": "Q23197"}
PRECISIONS = {"11": "day", "10": "month", "9": "year"}
WINDOW = timedelta(days=150)
NOTES = (
    "Mayors' terms as recorded in Wikidata (position held, P39, of the office each city or county names as its head "
    "of government, or a subclass of mayor whose jurisdiction is that place), with start and end dates from the "
    "statement's qualifiers. Places are found by their GNIS or FIPS codes. A term is shown as acting only where its "
    "statement says so."
)


def _codes() -> dict[str, list[str]]:
    keys: dict[str, list[str]] = defaultdict(list)
    for source, url, field, prefix in (
        (alged.SOURCE, alged.PLACES, "PLACENS", "place"),
        (census.SOURCE, census.COUNTIES, "COUNTYNS", "county"),
    ):
        with zipfile.ZipFile(raw.cached(source, url).path) as archive:
            dbf = next(name for name in archive.namelist() if name.lower().endswith(".dbf"))
            reader = shapefile.Reader(dbf=io.BytesIO(archive.read(dbf)))
            for record in reader.iterRecords():
                if record is None:
                    continue
                geoid = str(record["GEOID"])
                keys[f"{prefix}:{geoid}"] += [f"P590:{str(record[field]).lstrip('0')}"] + (
                    [f"P774:{geoid}", f"P774:{geoid[:2]}-{geoid[2:]}"] if prefix == "place" else [f"P882:{geoid}"]
                )
    keys["new-york-city"] = keys["place:3651000"]
    for polity, item in ITEMS.items():
        keys[polity].append(f"item:{item}")
    with connect() as conn:
        held = {
            value
            for (value,) in conn.execute(
                """select x.value from office o join external_id x on x.entity_id = o.polity_id and x.scheme = 'us.polity'
                   where o.kind = 'mayor' and not exists (select from entity_merge m where m.entity_id = o.id)"""
            ).fetchall()
        }
    return {polity: keys[polity] for polity in sorted(held) if polity in keys}


def _sparql(query: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})


def _lookups(codes: dict[str, list[str]]) -> list[str]:
    urls: list[str] = []
    for code in ("P590", "P774", "P882"):
        values = sorted({key.split(":", 1)[1] for keys in codes.values() for key in keys if key.startswith(f"{code}:")})
        for start in range(0, len(values), CHUNK):
            listed = " ".join(f'"{value}"' for value in values[start : start + CHUNK])
            urls.append(_sparql(LOOKUP.format(values=listed, code=code)))
    return urls


def _cities(codes: dict[str, list[str]]) -> dict[str, set[str]]:
    polities: dict[str, set[str]] = defaultdict(set)
    for polity, keys in codes.items():
        for key in keys:
            polities[key].add(polity)
    cities: dict[str, set[str]] = defaultdict(set)
    for url in _lookups(codes):
        code = "P590" if "P590" in url else "P774" if "P774" in url else "P882"
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text())["results"]["bindings"]:
            cities[binding["city"]["value"].rsplit("/", 1)[-1]] |= polities[f"{code}:{binding['value']['value']}"]
    for polity, item in ITEMS.items():
        if polity in codes:
            cities[item].add(polity)
    return cities


def _queries(cities: dict[str, set[str]]) -> list[str]:
    items = sorted(cities)
    return [
        _sparql(QUERY.format(items=" ".join(f"wd:{item}" for item in items[start : start + CHUNK // 2])))
        for start in range(0, len(items), CHUNK // 2)
    ]


def fetch() -> None:
    codes = _codes()
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "codes.json").write_text(json.dumps(codes, indent=0, sort_keys=True))
    for url in _lookups(codes):
        raw.fetch(SOURCE, url, suffix=".json")
    for url in _queries(_cities(codes)):
        raw.fetch(SOURCE, url, suffix=".json")


def parse() -> None:
    codes: dict[str, list[str]] = json.loads((PARSED / "codes.json").read_text())
    cities = _cities(codes)
    held: dict[str, dict[str, str]] = {}
    for url in _queries(cities):
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text())["results"]["bindings"]:
            row = {
                name: value["value"].rsplit("/", 1)[-1]
                for name, value in binding.items()
                if value["type"] != "uri" or "/.well-known/genid/" not in value["value"]
            }
            if len(row) > len(held.get(row["statement"], {})):
                held[row["statement"]] = row
    rows: list[dict[str, object]] = []
    for row in held.values():
        if not row.get("start", "")[:1].isdigit() or row.get("end", "").startswith("-"):
            continue
        rows += [
            {
                "polity": polity,
                "statement": row["statement"],
                "person": row["person"],
                "label": row.get("label", row["person"]),
                "start": date.fromisoformat(row["start"][:10]),
                "start_precision": PRECISIONS.get(row.get("startPrecision", ""), "unknown"),
                "end": date.fromisoformat(row["end"][:10]) if "end" in row else None,
                "end_precision": PRECISIONS.get(row.get("endPrecision", ""), "day"),
                "death": date.fromisoformat(row["death"][:10]) if row.get("death", "")[:1].isdigit() else None,
                "acting": any(word in row.get("roleLabel", "").lower() for word in ("acting", "interim")),
            }
            for polity in sorted(cities[row["city"]])
        ]
    print(f"{len(rows)} dated terms of {len(held)} statements")
    pl.DataFrame(
        rows, schema_overrides={"start": pl.Date, "end": pl.Date, "death": pl.Date, "acting": pl.Boolean}
    ).sort("polity", "start", "statement").write_parquet(PARSED / "tenures.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "tenures.parquet")
    codes: dict[str, list[str]] = json.loads((PARSED / "codes.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayors' tenures in US cities and counties from Wikidata, for the mayors whose candidacies the "
            "archive holds, linked to the elections they won",
            polity="US",
        )
        wikidata = cs.source("Wikidata", "Mayors of US cities and counties")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Mayors of US cities and counties",
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            notes=NOTES,
        )
        for url in [*_lookups(codes), *_queries(_cities(codes))]:
            cs.artifact(raw.cached(SOURCE, url), wikidata)
        offices: dict[str, UUID] = dict(
            conn.execute(
                """select x.value, o.id from office o join external_id x on x.entity_id = o.polity_id and x.scheme = 'us.polity'
                   where o.kind = 'mayor' and not exists (select from entity_merge m where m.entity_id = o.id)
                   order by o.id"""
            ).fetchall()
        )
        known: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'wikidata'").fetchall()
        )
        tagged = set(known.values())
        ran: dict[UUID, dict[UUID, set[str]]] = defaultdict(lambda: defaultdict(set))
        won: dict[UUID, list[tuple[date, UUID, UUID]]] = defaultdict(list)
        calendar: dict[UUID, list[date]] = defaultdict(list)
        for office, day, contest, person, ballot, preferred, elected, general in conn.execute(
            """select ct.office_id, el.date_from, ct.id, cd.person_id, cd.ballot_name,
                      (select n.text from name n where n.entity_id = cd.person_id and n.is_preferred limit 1),
                      exists (select from outcome oc join result_set r on r.id = oc.result_set_id and r.is_default
                              where oc.option_id = op.id and oc.kind in ('elected', 'won')),
                      el.kind <> 'primary' and ct.party_scope_id is null
               from contest ct
               join office o on o.id = ct.office_id and o.kind = 'mayor'
               join election el on el.id = ct.election_id
               join contest_option op on op.contest_id = ct.id
               join candidacy cd on cd.option_id = op.id and cd.role = 'head'
               where el.date_from is not null"""
        ).fetchall():
            person = cs.survivors.get(person, person)
            ran[office][person] |= {text for text in (ballot, preferred) if text}
            if general:
                calendar[office].append(day)
                if elected:
                    won[office].append((day, contest, person))
        unmatched: list[tuple[str, str, str, str, str]] = []
        linked: set[UUID] = set()
        previous: dict[UUID, UUID] = {}
        tenures = 0
        done: set[str] = set()
        for (
            polity,
            statement,
            qid,
            label,
            start,
            start_precision,
            end,
            end_precision,
            death,
            acting,
        ) in frame.iter_rows():
            office = offices.get(polity)
            if office is None or statement in done:
                continue
            candidates = ran[office]
            person = known.get(qid)
            if person not in candidates:
                hits = {found for found, texts in candidates.items() if any(compatible(label, text) for text in texts)}
                if len(hits) != 1:
                    unmatched.append((polity, qid, label, str(start), "no candidate" if not hits else "ambiguous"))
                    continue
                person = next(iter(hits))
                if qid in known:
                    unmatched.append((polity, qid, label, str(start), f"item held by {known[qid]}, matched {person}"))
                elif person not in tagged:
                    cs.add("external_id", entity_id=person, scheme="wikidata", value=qid)
                    known[qid] = person
                    tagged.add(person)
            assert person is not None
            match = next(
                (
                    contest
                    for day, contest, winner in won[office]
                    if winner == person and timedelta(0) <= start - day <= WINDOW and contest not in linked
                ),
                None,
            )
            if match is not None:
                linked.add(match)
            scheduled = any(timedelta(0) <= start - day <= WINDOW for day in calendar[office])
            covered = any(abs(start - day) <= timedelta(days=5 * 366) for day in calendar[office])
            tenure = cs.key(f"{SOURCE}.tenure", statement)
            done.add(statement)
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                office_id=office,
                from_date=start,
                from_precision=start_precision,
                to_date=end,
                to_precision=end_precision,
                acquired_via="acting"
                if acting
                else "elected"
                if match is not None or scheduled or not covered
                else "succession",
                contest_id=match,
                ended_via="died"
                if end is not None and death is not None and abs(death - end) <= timedelta(days=1)
                else None,
                succeeds_tenure_id=None if acting else previous.get(office),
            )
            cs.add("citation", entity_id=tenure, source_id=wikidata, locator={"statement": statement})
            if not acting:
                previous[office] = tenure
            tenures += 1
        LOG.write_text(
            "polity\titem\tlabel\tstart\treason\n" + "".join("\t".join(row) + "\n" for row in sorted(unmatched))
        )
        print(
            f"{tenures} tenures in {len({office for office in previous.values()})} offices; {len(unmatched)} terms unmatched, logged to {LOG}"
        )
        return cs.write()
