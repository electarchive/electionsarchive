import json
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "it.regional_people"
VERSION = "1"
TITLE = "Wikidata items of the candidates for president of an Italian region"
API = "https://www.wikidata.org/w/api.php?"
WIKI = "https://it.wikipedia.org/w/api.php?"
WINDOW = 200
PROPS = "labels|aliases|claims|sitelinks"
BATCH = 50
STEMS = {
    "Abruzzo": "abruzz",
    "Basilicata": "basilicat",
    "Calabria": "calabr",
    "Campania": "campan",
    "Emilia-Romagna": "emilia",
    "Lazio": "lazi",
    "Liguria": "ligur",
    "Lombardia": "lombard",
    "Marche": "marche",
    "Molise": "molis",
    "Piemonte": "piemont",
    "Puglia": "pugli",
    "Sicilia": "sicil",
    "Toscana": "toscan",
    "Umbria": "umbri",
    "Veneto": "venet",
}
CHAMBERS = (
    "chamber of deputies",
    "deputato",
    "senate of the",
    "senatore",
    "constituent assembly",
    "assemblea costituente",
)
REGIONAL = ("region", "president")
HEADS = """
select distinct k.person_id, n.text, r.text, extract(year from e.date_from)::int from candidacy k
join changeset c on c.id = k.changeset_id
join contest_option o on o.id = k.option_id
join contest t on t.id = o.contest_id
join election e on e.id = t.election_id
join name r on r.entity_id = e.polity_id and r.lang = 'it' and r.is_preferred
join name n on n.entity_id = k.person_id and n.is_preferred
where c.pipeline = 'it.regionali' and k.role = 'head'
  and not exists (select from entity_merge m where m.entity_id = k.person_id)
"""
NATIONAL = """
select k.person_id, array_agg(distinct extract(year from e.date_from)::int) from candidacy k
join contest_option o on o.id = k.option_id
join contest t on t.id = o.contest_id
join body b on b.id = t.body_id and b.kind in ('lower_house', 'upper_house', 'constituent_assembly')
join election e on e.id = t.election_id
join slug s on s.entity_id = e.polity_id and s.is_current and s.path = 'it'
where not exists (select from entity_merge m where m.entity_id = k.person_id)
group by 1
"""


def _fold(text: str) -> frozenset[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return frozenset(plain.replace("'", " ").replace("-", " ").split())


def _heads(conn) -> dict[UUID, tuple[str, set[tuple[str, int]]]]:
    heads: dict[UUID, tuple[str, set[tuple[str, int]]]] = {}
    for person, text, region, year in conn.execute(HEADS).fetchall():
        heads.setdefault(person, (text, set()))[1].add((region, year))
    return heads


def _search(text: str) -> str:
    return API + urlencode(
        {
            "action": "wbsearchentities",
            "search": text,
            "language": "it",
            "uselang": "it",
            "type": "item",
            "limit": 20,
            "format": "json",
        }
    )


def _entities(ids: list[str], props: str) -> list[str]:
    ordered = sorted(set(ids), key=lambda item: int(item[1:]))
    return [
        API
        + urlencode(
            {
                "action": "wbgetentities",
                "ids": "|".join(ordered[start : start + BATCH]),
                "props": props,
                "languages": "it|en",
                "sitefilter": "itwiki",
                "format": "json",
            }
        )
        for start in range(0, len(ordered), BATCH)
    ]


def _read(url: str) -> dict:
    return json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))


def _get(urls: list[str]) -> None:
    held = raw.keys(SOURCE)
    for url in urls:
        if url not in held:
            raw.fetch(SOURCE, url, suffix=".json")
            time.sleep(0.5)


def _hits(heads: dict[UUID, tuple[str, set[tuple[str, int]]]]) -> dict[UUID, set[str]]:
    hits: dict[UUID, set[str]] = {}
    for person, (text, _) in heads.items():
        found = _read(_search(text)).get("search", [])
        hits[person] = {hit["id"] for hit in found if _fold(hit.get("match", {}).get("text", "")) == _fold(text)}
    return hits


def _values(entity: dict, prop: str) -> list[tuple[str, int | None]]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim["mainsnak"].get("datavalue", {}).get("value")
        if not isinstance(value, dict) or "id" not in value:
            continue
        start = None
        for qualifier in claim.get("qualifiers", {}).get("P580", []):
            when = qualifier.get("datavalue", {}).get("value", {}).get("time", "")
            if when[1:5].isdigit():
                start = int(when[1:5])
        out.append((value["id"], start))
    return out


def _labels(entity: dict) -> set[frozenset[str]]:
    texts = [entry["value"] for entry in entity.get("labels", {}).values()]
    texts += [entry["value"] for entries in entity.get("aliases", {}).values() for entry in entries]
    return {_fold(text) for text in texts}


def fetch() -> None:
    with connect() as conn:
        heads = _heads(conn)
        known = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x where x.scheme = 'wikidata' and x.entity_id = any(%s)""",
                [list(_pool(conn, heads))],
            ).fetchall()
        }
    _get([_search(text) for text, _ in heads.values()])
    items = sorted({item for found in _hits(heads).values() for item in found} | known)
    _get(_entities(items, PROPS))
    _get(_entities(sorted(_mentioned(items)), "labels"))
    _get(_articles(_loaded(items)))


def _pool(conn, heads: dict[UUID, tuple[str, set[tuple[str, int]]]]) -> dict[UUID, list[int]]:
    wanted = {_fold(text) for text, _ in heads.values()}
    national = dict(conn.execute(NATIONAL).fetchall())
    names = conn.execute("select entity_id, text from name where entity_id = any(%s)", [list(national)]).fetchall()
    return {person: national[person] for person, text in names if _fold(text) in wanted and person not in heads}


def _loaded(items: list[str]) -> dict[str, dict]:
    entities: dict[str, dict] = {}
    for url in _entities(items, PROPS):
        entities.update(_read(url).get("entities", {}))
    return entities


def _article(title: str) -> str:
    return WIKI + urlencode(
        {"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title, "format": "json"}
    )


def _articles(entities: dict[str, dict]) -> list[str]:
    return sorted(
        {
            _article(entity["sitelinks"]["itwiki"]["title"])
            for entity in entities.values()
            if "itwiki" in entity.get("sitelinks", {})
        }
    )


def _text(entity: dict) -> str:
    if "itwiki" not in entity.get("sitelinks", {}):
        return ""
    pages = _read(_article(entity["sitelinks"]["itwiki"]["title"])).get("query", {}).get("pages", {})
    return " ".join(page.get("extract", "") for page in pages.values()).lower()


def _told(text: str, races: set[tuple[str, int]]) -> bool:
    for region, year in races:
        start = text.find(str(year))
        while start != -1:
            window = text[max(0, start - WINDOW) : start + WINDOW]
            if "regional" in window and region.lower() in window:
                return True
            start = text.find(str(year), start + 1)
    return False


def _mentioned(items: list[str]) -> set[str]:
    entities = _loaded(items)
    return {value for entity in entities.values() for prop in ("P39", "P3602") for value, _ in _values(entity, prop)}


def emit() -> Path:
    with connect() as conn:
        heads = _heads(conn)
        pool = _pool(conn, heads)
        named: dict[frozenset[str], list[UUID]] = defaultdict(list)
        for person, text in conn.execute(
            "select entity_id, text from name where entity_id = any(%s)", [list(pool)]
        ).fetchall():
            if person not in named[_fold(text)]:
                named[_fold(text)].append(person)
        held = dict(
            conn.execute(
                """select x.value, x.entity_id from external_id x
                   where x.scheme = 'wikidata' and exists (select from person p where p.id = x.entity_id)"""
            ).fetchall()
        )
        own = defaultdict(set)
        for item, person in held.items():
            own[person].add(item)
        hits = _hits(heads)
        items = sorted({item for found in hits.values() for item in found} | {i for p in pool for i in own[p]})
        entities = _loaded(items)
        captions: dict[str, str] = {}
        for url in _entities(sorted(_mentioned(items)), "labels"):
            for key, entity in _read(url).get("entities", {}).items():
                captions[key] = " | ".join(entry["value"].lower() for entry in entity.get("labels", {}).values())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Candidates for president of an Italian region matched to their Wikidata items, where the item's label "
                "is their name and it held a regional office or stood in a regional election in that region; each is "
                "merged into the person who already holds the item, or into the Chamber or Senate candidate of the same "
                "name whose seat the item held from that election's year; an item with no such office or candidacy fits "
                "when its Italian Wikipedia article names a regional election in that region and year"
            ),
            polity=None,
            kind="match",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in [
            *(_search(text) for text, _ in heads.values()),
            *_entities(items, PROPS),
            *_entities(sorted(_mentioned(items)), "labels"),
            *_articles(entities),
        ]:
            cs.artifact(raw.cached(SOURCE, url), source)
        report: list[str] = []
        linked = merged = ambiguous = 0
        for person, (text, races) in sorted(heads.items(), key=lambda entry: entry[1][0]):
            stems = [STEMS[region] for region, _ in races if region in STEMS]
            fitting = [
                item
                for item in sorted(hits[person])
                if item in entities
                and _fold(text) in _labels(entities[item])
                and "Q5" in {value for value, _ in _values(entities[item], "P31")}
                and (
                    any(
                        any(word in captions.get(value, "") for word in REGIONAL)
                        and any(stem in captions.get(value, "") for stem in stems)
                        for prop in ("P39", "P3602")
                        for value, _ in _values(entities[item], prop)
                    )
                    or _told(_text(entities[item]), races)
                )
            ]
            if len(fitting) != 1:
                ambiguous += len(fitting) > 1
                continue
            item = fitting[0]
            seats = {
                start
                for value, start in _values(entities[item], "P39")
                if start is not None and any(chamber in captions.get(value, "") for chamber in CHAMBERS)
            }
            same = [
                other
                for other in named.get(_fold(text), [])
                if own[other] == {item}
                or (not own[other] and any(year in seats or year + 1 in seats for year in pool[other]))
            ]
            holder = held.get(item)
            targets = [cs.survivors.get(other, other) for other in ([holder] if holder is not None else []) + same]
            survivor = targets[0] if targets else person
            for other in dict.fromkeys([person, *targets[1:]]):
                if cs.survivors.get(other, other) != survivor:
                    cs.merge(other, survivor)
                    merged += 1
                    report.append(f"{other}\t{text}\t{item}\tmerged into {survivor}")
            if holder is None:
                cs.add("external_id", entity_id=survivor, scheme="wikidata", value=item)
                cs.add("citation", entity_id=survivor, source_id=source, locator={"item": item})
                held[item] = survivor
                linked += 1
                report.append(f"{survivor}\t{text}\t{item}\tlinked")
        (DATA / "logs" / "it_regional_people.tsv").write_text("".join(f"{line}\n" for line in report), encoding="utf-8")
        print(f"{len(heads)} heads: {linked} linked, {merged} merged, {ambiguous} with more than one fitting item")
        return cs.write()
