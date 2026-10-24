import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "wikidata_members"
VERSION = "2"
INDEPENDENT = "Q327591"
TITLE = "Party memberships (P102) of people"
MEMBERSHIPS = """SELECT ?item ?party ?start ?sp ?end ?ep WHERE {{ VALUES ?item {{ {items} }}
?item p:P102 ?s . ?s ps:P102 ?party ; wikibase:rank ?rank . FILTER(?rank != wikibase:DeprecatedRank)
OPTIONAL {{ ?s pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?sp ] }}
OPTIONAL {{ ?s pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?ep ] }} }}"""
CHUNK = 400
PRECISIONS = {11: "day", 10: "month", 9: "year"}


def _urls() -> list[str]:
    with connect() as conn:
        items = sorted(
            {
                value
                for (value,) in conn.execute(
                    """select x.value from external_id x join person p on p.id = x.entity_id
                       where x.scheme = 'wikidata'
                         and not exists (select from entity_merge m where m.entity_id = p.id)"""
                ).fetchall()
            },
            key=lambda item: int(item[1:]),
        )
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": MEMBERSHIPS.format(items=" ".join(f"wd:{item}" for item in items[start : start + CHUNK])),
                "format": "json",
            }
        )
        for start in range(0, len(items), CHUNK)
    ]


def fetch() -> None:
    for url in _urls():
        raw.fetch(SOURCE, url, suffix=".json")


def emit() -> Path:
    urls = _urls()
    statements: dict[str, list[tuple[str, date | None, str, date | None, str]]] = defaultdict(list)
    for url in urls:
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            bounds: list[tuple[date | None, str]] = []
            for value, precision in (("start", "sp"), ("end", "ep")):
                text = binding.get(value, {}).get("value", "")
                exact = PRECISIONS.get(int(binding.get(precision, {}).get("value", "0")))
                try:
                    day = date.fromisoformat(text[:10]) if exact and text[:1].isdigit() else None
                except ValueError:
                    day = None
                if day is not None and value == "end":
                    day = (
                        day + timedelta(days=1)
                        if exact == "day"
                        else date(day.year + day.month // 12, day.month % 12 + 1, 1)
                        if exact == "month"
                        else date(day.year + 1, 1, 1)
                    )
                bounds.append((day, exact if day is not None else "unknown"))
            statements[binding["item"]["value"].rsplit("/", 1)[1]].append(
                (binding["party"]["value"].rsplit("/", 1)[1], *bounds[0], *bounds[1])
            )
    with connect() as conn:
        people: dict[str, list[UUID]] = defaultdict(list)
        for person, item in conn.execute(
            """select p.id, x.value from person p join external_id x on x.entity_id = p.id and x.scheme = 'wikidata'
               where not exists (select from entity_merge m where m.entity_id = p.id)"""
        ).fetchall():
            people[item].append(person)
        parties: dict[str, list[UUID]] = defaultdict(list)
        for party, item in conn.execute(
            """select p.id, x.value from party p join external_id x on x.entity_id = p.id and x.scheme = 'wikidata'
               where not exists (select from entity_merge m where m.entity_id = p.id)"""
        ).fetchall():
            parties[item].append(party)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Party memberships from Wikidata for people with a Wikidata item, dated where Wikidata dates them, "
                "and undated only where Wikidata names one party; dated spells as an independent politician carry no party"
            ),
            polity=None,
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Property:P102",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in urls:
            cs.artifact(raw.cached(SOURCE, url), source)
        added = unmatched = undated = 0
        for item, rows in statements.items():
            if len(people[item]) != 1:
                continue
            named = {party for party, *_ in rows}
            spans: dict[UUID | None, list[tuple[date | None, str, date | None, str]]] = defaultdict(list)
            for party, start, start_precision, end, end_precision in rows:
                if party == INDEPENDENT:
                    if start is None and end is None:
                        undated += 1
                    else:
                        spans[None].append((start, start_precision, end, end_precision))
                    continue
                if len(parties[party]) != 1:
                    unmatched += 1
                    continue
                dated = any(other == party and (s or e) for other, s, _, e, _ in rows)
                if start is None and end is None and (len(named) > 1 or dated):
                    undated += 1
                    continue
                spans[parties[party][0]].append((start, start_precision, end, end_precision))
            for party, ranges in spans.items():
                ranges.sort(key=lambda span: span[0] or date.min)
                joined: list[list[object]] = []
                for start, start_precision, end, end_precision in ranges:
                    last = joined[-1] if joined else None
                    if last is not None and (last[2] is None or start is None or start <= last[2]):
                        if last[2] is not None and (end is None or end > last[2]):
                            last[2], last[3] = end, end_precision
                        continue
                    joined.append([start, start_precision, end, end_precision])
                for start, start_precision, end, end_precision in joined:
                    cs.add(
                        "person_party_membership",
                        person_id=people[item][0],
                        party_id=party,
                        valid_from=start,
                        valid_from_precision=start_precision,
                        valid_to=end,
                        valid_to_precision=end_precision,
                        source_id=source,
                    )
                    added += 1
        print(
            f"{added} memberships for {len(statements)} items, {unmatched} statements on parties the archive lacks, "
            f"{undated} undated statements left out beside other parties"
        )
        return cs.write()
