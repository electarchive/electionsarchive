import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.tenures import ENDED
from ea.store import connect

SOURCE = "it.senato_unelected"
VERSION = "1"
SENATE = UUID("f024c5c6-4cf2-5173-b216-423d63f946b4")
FIRST = (date(1948, 5, 8), date(1953, 6, 24))
LIFE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?name ?start ?end ?by ?cause (MIN(?began) AS ?president) WHERE {
?item p:P39 ?held . ?held ps:P39 wd:Q826589 .
OPTIONAL { ?held pq:P580 ?start } OPTIONAL { ?held pq:P582 ?end } OPTIONAL { ?held pq:P748 ?by } OPTIONAL { ?held pq:P1534 ?cause }
OPTIONAL { ?item p:P39 ?led . ?led ps:P39 wd:Q332711 ; pq:P580 ?began }
OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = "it") }
} GROUP BY ?item ?name ?start ?end ?by ?cause""",
        "format": "json",
    }
)
ROLL = "https://it.wikipedia.org/w/api.php?" + urlencode(
    {
        "action": "parse",
        "page": "Senatori della I legislatura della Repubblica Italiana",
        "prop": "wikitext",
        "format": "json",
        "formatversion": "2",
    }
)


def right() -> list[tuple[str, str]]:
    text = json.loads(raw.cached(SOURCE, ROLL).path.read_text(encoding="utf-8"))["parse"]["wikitext"]
    roll = text[text.index("==Composizione storica==") : text.index("==Modifiche intervenute")]
    return [
        (f"{first} {last}", title or f"{first} {last}")
        for first, last, title in re.findall(
            r"\{\{sortname\|([^|}]+)\|([^|}]+)(?:\|([^|}]+))?\}\}\s*\n\|\s*Di diritto", roll
        )
    ]


def pages(titles: list[str]) -> list[str]:
    return [
        "https://it.wikipedia.org/w/api.php?"
        + urlencode(
            {
                "action": "query",
                "prop": "pageprops",
                "ppprop": "wikibase_item",
                "redirects": "1",
                "titles": "|".join(titles[start : start + 50]),
                "format": "json",
                "formatversion": "2",
            }
        )
        for start in range(0, len(titles), 50)
    ]


def deaths(items: list[str]) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {
            "query": f"SELECT ?item ?died WHERE {{ VALUES ?item {{ {' '.join(f'wd:{item}' for item in items)} }} ?item wdt:P570 ?died }}",
            "format": "json",
        }
    )


def items() -> dict[str, str]:
    found: dict[str, str] = {}
    for url in pages([title for _, title in right()]):
        query = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["query"]
        aliases = {entry["to"]: entry["from"] for entry in query.get("normalized", []) + query.get("redirects", [])}
        for page in query["pages"]:
            title = page["title"]
            while title in aliases:
                title = aliases[title]
            if "pageprops" in page:
                found[title] = page["pageprops"]["wikibase_item"]
    return found


def everyone() -> list[str]:
    life = json.loads(raw.cached(SOURCE, LIFE).path.read_text(encoding="utf-8"))["results"]["bindings"]
    return sorted({*items().values(), *(binding["item"]["value"].rsplit("/", 1)[1] for binding in life)})


def fetch() -> None:
    raw.fetch(SOURCE, LIFE, suffix=".json")
    raw.fetch(SOURCE, ROLL, suffix=".json")
    for url in pages([title for _, title in right()]):
        raw.fetch(SOURCE, url, suffix=".json")
    raw.fetch(SOURCE, deaths(everyone()), suffix=".json")


def emit() -> Path:
    def day(binding: dict[str, dict[str, str]], key: str) -> date | None:
        value = binding.get(key, {}).get("value", "")[:10]
        return date.fromisoformat(value) if value[:1].isdigit() else None

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italy's senators for life and former presidents (Wikidata) and the 107 senators by right of 1948–1953 (Italian Wikipedia), as Senate tenures",
            polity="IT",
        )
        wikidata = cs.source("Wikidata", "Italian senators for life (P39 Q826589)")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Italian senators for life (P39 Q826589)",
            url="https://www.wikidata.org/wiki/Q826589",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        wikipedia = cs.source("Wikipedia", "Senatori della I legislatura della Repubblica Italiana")
        cs.add(
            "source",
            id=wikipedia,
            kind="community",
            publisher="Wikipedia",
            title="Senatori della I legislatura della Repubblica Italiana",
            url="https://it.wikipedia.org/wiki/Senatori_della_I_legislatura_della_Repubblica_Italiana",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        )
        cs.artifact(raw.cached(SOURCE, LIFE), wikidata)
        cs.artifact(raw.cached(SOURCE, ROLL), wikipedia)
        linked = items()
        died = {
            binding["item"]["value"].rsplit("/", 1)[1]: day(binding, "died")
            for binding in json.loads(raw.cached(SOURCE, deaths(everyone())).path.read_text(encoding="utf-8"))[
                "results"
            ]["bindings"]
        }
        life = json.loads(raw.cached(SOURCE, LIFE).path.read_text(encoding="utf-8"))["results"]["bindings"]
        known = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'wikidata' and value = any(%s)",
                [[*linked.values(), *(binding["item"]["value"].rsplit("/", 1)[1] for binding in life)]],
            ).fetchall()
        }
        named: dict[str, tuple[UUID, date]] = {}
        for text, person, last, _ in conn.execute(
            """select n.text, cy.person_id, max(e.date_from), count(*) from candidacy cy
               join contest_option o on o.id = cy.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where e.polity_id = %s and not exists (select from entity_merge m where m.entity_id = cy.person_id)
               group by 1, 2 order by 1, 4""",
            [cs.polity("IT")],
        ).fetchall():
            named[text.casefold()] = (person, last)

        def person(item: str | None, name: str, key: str) -> UUID:
            if item is not None and item in known:
                return known[item]
            found = named.get(name.casefold())
            end = died.get(item or "")
            match = None if found is None or (end is not None and found[1] > end) else found[0]
            if match is not None:
                if item is not None:
                    cs.add("external_id", entity_id=match, scheme="wikidata", value=item)
                    known[item] = match
                return match
            id = cs.key("wikidata", item) if item is not None else cs.key(f"{SOURCE}.person", key)
            if id in cs.minted:
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=name, lang="it", kind="official", is_preferred=True)
            if item is not None:
                known[item] = id
            return id

        for binding in life:
            item = binding["item"]["value"].rsplit("/", 1)[1]
            start, end, president = day(binding, "start"), day(binding, "end"), day(binding, "president")
            if start is None:
                continue
            cause = binding.get("cause", {}).get("value", "").rsplit("/", 1)[-1]
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"life:{item}:{start}"),
                person_id=person(item, binding.get("name", {}).get("value", item), item),
                office_id=None,
                body_id=SENATE,
                from_date=start,
                from_precision="day",
                to_date=end,
                to_precision="day",
                acquired_via="ex_officio"
                if "by" not in binding and president is not None and president <= start
                else "appointed",
                ended_via=None
                if end is None
                else ENDED.get(cause) or ("elevated" if president is not None and president >= end else "died"),
            )
        for name, title in right():
            item = linked.get(title)
            end = died.get(item or "")
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"right:{title}"),
                person_id=person(item, name, title),
                office_id=None,
                body_id=SENATE,
                from_date=FIRST[0],
                from_precision="day",
                to_date=end if end is not None and end < FIRST[1] else FIRST[1],
                to_precision="day",
                acquired_via="ex_officio",
                ended_via="died" if end is not None and end < FIRST[1] else "term_end",
            )
        return cs.write()
