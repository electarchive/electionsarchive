import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx

from ea import names, raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "wikidata_match"
VERSION = "4"
TITLE = "Labels and positions held (P39) of people"
BUCKETS = 300
SLACK = 1
KINDS = ("governor", "head_of_state", "head_of_government", "mayor", "upper_house", "lower_house", "unicameral")
STOP = {"of", "the", "de", "la", "du", "des", "and"}
COUNTRIES = {
    "gb": ("united kingdom", "british"),
    "ca": ("canada", "canadian"),
    "fr": ("france", "french"),
    "it": ("italy", "italian"),
    "br": ("brazil", "brazilian"),
    "de": ("germany", "german", "bundestag", "landtag"),
    "tr": ("turkey", "türkiye", "turkish", "grand national assembly"),
    "ch": ("switzerland", "swiss"),
    "ie": ("ireland", "irish", "dáil"),
}
ALIASES = {"house of commons": {"gb": "parliament of the united kingdom"}}
QUERY = """SELECT ?name ?item ?posLabel ?start ?end WHERE {{ VALUES ?name {{ {names} }}
?item rdfs:label|skos:altLabel ?name ; wdt:P31 wd:Q5 ; p:P39 ?s . ?s ps:P39 ?pos .
OPTIONAL {{ ?s pq:P580 ?start }} OPTIONAL {{ ?s pq:P582 ?end }}
SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return {word for word in re.findall(r"[a-z0-9]+", plain) if word not in STOP}


def _literal(text: str, lang: str) -> str:
    return json.dumps(text, ensure_ascii=False) + "@" + lang


def _targets(conn) -> dict[UUID, dict]:
    targets: dict[UUID, dict] = {}
    for person, holder, country, year in conn.execute(
        """
        select distinct cy.person_id, n.text, split_part(s.path, '/', 1), extract(year from e.date_from)::int
        from outcome x
        join result_set rs on rs.id = x.result_set_id and rs.is_default
        join contest_option co on co.id = x.option_id
        join candidacy cy on cy.option_id = co.id and cy.role = 'head'
        join contest c on c.id = co.contest_id
        join election e on e.id = c.election_id
        join slug s on s.entity_id = e.polity_id and s.is_current
        left join office o on o.id = c.office_id
        left join body b on b.id = c.body_id
        join name n on n.entity_id = coalesce(c.office_id, c.body_id) and n.kind = 'official' and n.lang = 'en'
        where x.kind in ('elected', 'won') and e.date_from >= '2016-01-01' and c.party_scope_id is null
          and coalesce(o.kind::text, b.kind::text) = any(%s)
          and not exists (select from external_id z where z.entity_id = cy.person_id and z.scheme = 'wikidata')
        """,
        (list(KINDS),),
    ).fetchall():
        targets.setdefault(person, {"names": set(), "wins": set()})["wins"].add((holder, country, year))
    for person, text, lang in conn.execute(
        "select entity_id, text, lang from name where entity_id = any(%s) and kind in ('official', 'english')",
        (list(targets),),
    ).fetchall():
        for spelling in {text, names.PARTICLE.sub(lambda match: match[0][0].swapcase() + match[0][1:], text)}:
            targets[person]["names"] |= {(spelling, "en"), (spelling, lang)} if len(lang) == 2 else {(spelling, "en")}
    return targets


def _urls(targets: dict[UUID, dict]) -> list[str]:
    buckets: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for person, target in targets.items():
        buckets[person.int % BUCKETS] |= target["names"]
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": QUERY.format(names=" ".join(_literal(text, lang) for text, lang in sorted(buckets[bucket]))),
                "format": "json",
            }
        )
        for bucket in sorted(buckets)
    ]


def _holds(label: str, holder: str, country: str) -> bool:
    alias = ALIASES.get(holder.lower(), {}).get(country)
    if alias is not None and alias in label:
        return True
    if not _words(holder) <= _words(label):
        return False
    return (
        country == "us"
        or any(word in label for word in COUNTRIES.get(country, ()))
        or holder.lower().startswith("mayor of ")
    )


def fetch() -> None:
    with connect() as conn:
        urls = _urls(_targets(conn))
    failed = 0
    for url in urls:
        try:
            raw.fetch(SOURCE, url, suffix=".json")
        except httpx.HTTPError:
            failed += 1
    print(f"{len(urls) - failed} of {len(urls)} batches fetched; rerun to retry the rest")


def emit() -> Path:
    with connect() as conn:
        targets = _targets(conn)
        urls = _urls(targets)
        found: dict[tuple[str, str], dict[str, list[tuple[str, int, int]]]] = defaultdict(lambda: defaultdict(list))
        fetched: list[str] = []
        for url in urls:
            try:
                artifact = raw.cached(SOURCE, url)
            except LookupError:
                continue
            fetched.append(url)
            for binding in json.loads(artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
                start = binding.get("start", {}).get("value", "")
                end = binding.get("end", {}).get("value", "")
                found[(binding["name"]["value"], binding["name"].get("xml:lang", "en"))][
                    binding["item"]["value"].rsplit("/", 1)[1]
                ].append(
                    (
                        binding["posLabel"]["value"].lower(),
                        int(start[:4]) if start[:4].isdigit() else -9999,
                        int(end[:4]) if end[:4].isdigit() else 9999,
                    )
                )
        held = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'wikidata'"
            ).fetchall()
        }
        matches: dict[UUID, set[str]] = {}
        for person, target in targets.items():
            items: set[str] = set()
            for name in target["names"]:
                for item, positions in found.get(name, {}).items():
                    if any(
                        _holds(label, holder, country)
                        and (start != -9999 or end != 9999)
                        and start <= year + SLACK
                        and end >= year - SLACK
                        for label, start, end in positions
                        for holder, country, year in target["wins"]
                    ):
                        items.add(item)
            if items:
                matches[person] = items
        claimants: dict[str, set[UUID]] = defaultdict(set)
        for person, items in matches.items():
            for item in items:
                claimants[item].add(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Wikidata items for people without one who won an executive office or a legislative seat since 2016, "
                "where the item's label is their name and it held that office or chamber within a year of the win; "
                "a winner whose item another person already holds, or another winner also claims, is merged into that person"
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
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in fetched:
            cs.artifact(raw.cached(SOURCE, url), source)
        linked = ambiguous = 0
        joined: list[str] = []
        for person, items in sorted(matches.items()):
            item = next(iter(items))
            if len(items) > 1 or any(len(matches[other]) > 1 for other in claimants[item]):
                ambiguous += 1
                continue
            holder = held.get(item, min(claimants[item]))
            holder = cs.survivors.get(holder, holder)
            if holder == person:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                linked += 1
            elif person not in cs.survivors:
                cs.merge(person, holder)
                joined.append(
                    f"{person}\t{holder}\t{item}\t{' | '.join(sorted(text for text, _ in targets[person]['names']))}\n"
                )
        (DATA / "logs" / "wikidata_match_joined.tsv").write_text("".join(joined), encoding="utf-8")
        print(
            f"{len(targets)} people without an item, {len(fetched)} of {len(urls)} batches fetched: "
            f"{linked} linked, {ambiguous} ambiguous, {len(joined)} merged into the person holding or taking the item"
        )
        return cs.write()
