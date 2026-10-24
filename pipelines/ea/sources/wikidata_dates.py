import json
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "wikidata_dates"
VERSION = "1"
TITLE = "Dates of birth and death of people"
DATES = """SELECT ?item ?kind ?time ?precision ?rank WHERE {{
  VALUES ?item {{ {items} }}
  VALUES (?claim ?value ?kind) {{ (p:P569 psv:P569 "birth") (p:P570 psv:P570 "death") }}
  ?item ?claim ?st .
  ?st ?value ?v ; wikibase:rank ?rank .
  ?v wikibase:timeValue ?time ; wikibase:timePrecision ?precision .
  FILTER(?rank != wikibase:DeprecatedRank)
}}"""
BATCHES = 200
PRECISIONS = {"11": "day", "10": "month", "9": "year"}


def _urls() -> list[str]:
    with connect() as conn:
        items = sorted(
            {
                value
                for (value,) in conn.execute(
                    """select x.value from external_id x join person p on p.id = x.entity_id
                       where x.scheme = 'wikidata' and not exists (select from entity_merge m where m.entity_id = p.id)"""
                ).fetchall()
            },
            key=lambda item: int(item[1:]),
        )
    batches: dict[int, list[str]] = {}
    for item in items:
        batches.setdefault(int(item[1:]) % BATCHES, []).append(item)
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": DATES.format(items=" ".join(f"wd:{item}" for item in batches[batch])),
                "format": "json",
            }
        )
        for batch in sorted(batches)
    ]


def fetch() -> None:
    for url in _urls():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=".json")
            time.sleep(2)


def emit() -> Path:
    claims: dict[tuple[str, str], list[tuple[bool, int, str]]] = {}
    urls: list[str] = []
    for url in _urls():
        try:
            artifact = raw.cached(SOURCE, url)
        except LookupError:
            continue
        urls.append(url)
        for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
            precision = binding["precision"]["value"]
            if precision not in PRECISIONS or binding["time"]["value"].startswith("-"):
                continue
            claims.setdefault((binding["item"]["value"].rsplit("/", 1)[1], binding["kind"]["value"]), []).append(
                (
                    binding["rank"]["value"].endswith("PreferredRank"),
                    int(precision),
                    binding["time"]["value"][:10].replace("-00", "-01"),
                )
            )
    best: dict[tuple[str, str], tuple[str, str]] = {}
    split: list[str] = []
    for (item, kind), values in claims.items():
        top = [value for value in values if value[0] == max(rank for rank, *_ in values)]
        if len({text[:4] for *_, text in top}) > 1:
            split.append(f"{item}\t{kind}\t{' | '.join(sorted({text for *_, text in top}))}")
            continue
        _, precision, text = max(top)
        best[item, kind] = (text, PRECISIONS[str(precision)])
    with connect() as conn:
        people = conn.execute(
            """
            select p.id::text, x.value, p.birth_date::text, p.birth_precision::text, p.death_date::text,
                   p.death_precision::text,
                   (select max(e.date_from)::text from candidacy cy join contest_option o on o.id = cy.option_id
                    join contest c on c.id = o.contest_id join election e on e.id = c.election_id
                    where cy.person_id = p.id)
            from person p join external_id x on x.entity_id = p.id and x.scheme = 'wikidata'
            where not exists (select from entity_merge m where m.entity_id = p.id)
            """
        ).fetchall()
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Dates of death from Wikidata for every person with a Wikidata item, "
                "and dates of birth where the archive has none"
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
            url="https://www.wikidata.org/wiki/Property:P570",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in urls:
            cs.artifact(raw.cached(SOURCE, url), source)
        today = date.today().isoformat()
        held: list[str] = []
        deaths = births = 0
        for entity, item, born, born_precision, died, died_precision, ran in people:
            birth = best.get((item, "birth"))
            death = best.get((item, "death"))
            changed = False
            if born is None and birth is not None:
                born, born_precision = birth
                births += 1
                changed = True
            if death is not None and (death[0] > today or (born is not None and death[0] < born)):
                held.append(f"{entity}\t{item}\tdied {death[0]}, born {born}")
            elif death is not None and ran is not None and int(death[0][:4]) < int(ran[:4]) - 1:
                held.append(f"{entity}\t{item}\tdied {death[0]}, ran {ran}")
            elif death is not None and (died, died_precision) != death:
                died, died_precision = death
                deaths += 1
                changed = True
            if changed:
                cs.add(
                    "person",
                    id=entity,
                    birth_date=born,
                    birth_precision=born_precision,
                    death_date=died,
                    death_precision=died_precision,
                )
        log = DATA / "logs" / "wikidata_dates_held.tsv"
        log.write_text("".join(row + "\n" for row in sorted(held + split)), encoding="utf-8")
        print(
            f"{deaths} dates of death and {births} dates of birth for {len(people)} people from {len(urls)} of {BATCHES} batches, "
            f"{len(held)} held and {len(split)} with disagreeing claims in {log}"
        )
        return cs.write()
