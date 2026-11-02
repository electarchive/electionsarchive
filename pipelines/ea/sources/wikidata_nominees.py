import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx

from ea import names, raw
from ea.changeset import Changeset
from ea.sources.wikidata_match import BUCKETS, QUERY, SLACK, _holds, _literal, _words
from ea.store import DATA, connect

SOURCE = "wikidata_nominees"
VERSION = "1"
TITLE = "Candidacies in the 2026 elections (P3602) and positions held (P39) of people"
YEAR = 2026
KINDS = ("governor", "upper_house", "lower_house", "unicameral", "other")
PINNED = """SELECT DISTINCT ?item ?name ?electionLabel WHERE {
?election wdt:P17 wd:Q30 ; rdfs:label ?electionLabel . FILTER(LANG(?electionLabel) = "en" && STRSTARTS(?electionLabel, "2026 "))
?item wdt:P3602 ?election ; wdt:P31 wd:Q5 ; rdfs:label|skos:altLabel ?name . FILTER(LANG(?name) = "en") }"""
PINNED_URL = "https://query.wikidata.org/sparql?" + urlencode({"query": PINNED, "format": "json"})
US_OFFICE = {"united", "states"}


def _targets(conn) -> dict[UUID, dict]:
    targets: dict[UUID, dict] = {}
    for person, holder, state in conn.execute(
        """
        select distinct cy.person_id, n.text, sn.text
        from election e
        join polity p on p.id = e.polity_id and p.kind in ('federal_unit', 'region')
        join slug s on s.entity_id = e.polity_id and s.is_current and s.path like 'us/%%' and s.path not like 'us/%%/%%'
        join name sn on sn.entity_id = e.polity_id and sn.kind = 'official' and sn.lang = 'en'
        join contest c on c.election_id = e.id
        left join office o on o.id = c.office_id
        left join body b on b.id = c.body_id
        join name n on n.entity_id = coalesce(c.office_id, c.body_id) and n.kind = 'official' and n.lang = 'en'
        join contest_option co on co.contest_id = c.id
        join candidacy cy on cy.option_id = co.id and cy.role = 'head'
        where (e.date_from = '2026-11-03' or (e.date_from between '2026-01-01' and '2026-10-31' and e.kind = 'primary'))
          and coalesce(o.kind::text, b.kind::text) = any(%s)
          and n.text not like 'Judge of %%'
          and not exists (select from external_id z where z.entity_id = cy.person_id and z.scheme = 'wikidata')
        """,
        (list(KINDS),),
    ).fetchall():
        targets.setdefault(person, {"names": set(), "holders": set()})["holders"].add((holder, state))
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


def _pins(label: str, holder: str, state: str) -> bool:
    words = _words(label)
    if not _words(state) <= words:
        return False
    held = _words(holder)
    if "governor" in held:
        return "gubernatorial" in words and ("lieutenant" in held) == ("lieutenant" in words)
    if held >= US_OFFICE:
        return held <= words or ("house" in held and "congressional" in words and "district" in words)
    return held <= words and not US_OFFICE <= words


def fetch() -> None:
    with connect() as conn:
        urls = [PINNED_URL, *_urls(_targets(conn))]
    failed = 0
    for url in urls:
        try:
            raw.fetch(SOURCE, url, suffix=".json")
        except httpx.HTTPError:
            failed += 1
    print(f"{len(urls) - failed} of {len(urls)} queries fetched; rerun to retry the rest")


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
        pinned_artifact = raw.cached(SOURCE, PINNED_URL)
        pinned: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for binding in json.loads(pinned_artifact.path.read_text(encoding="utf-8"))["results"]["bindings"]:
            pinned[binding["name"]["value"]][binding["item"]["value"].rsplit("/", 1)[1]].add(
                binding["electionLabel"]["value"]
            )
        held = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'wikidata'"
            ).fetchall()
        }
        matches: dict[UUID, dict[str, str]] = {}
        for person, target in targets.items():
            by_pin: set[str] = set()
            by_office: set[str] = set()
            for name, _ in target["names"]:
                for item, labels in pinned.get(name, {}).items():
                    if any(_pins(label, holder, state) for label in labels for holder, state in target["holders"]):
                        by_pin.add(item)
            for name in target["names"]:
                for item, positions in found.get(name, {}).items():
                    if any(
                        _holds(label, holder, "us")
                        and (start != -9999 or end != 9999)
                        and start <= YEAR + SLACK
                        and end >= YEAR - SLACK
                        for label, start, end in positions
                        for holder, _ in target["holders"]
                    ):
                        by_office.add(item)
            items = by_pin or by_office
            if items:
                matches[person] = dict.fromkeys(items, "P3602" if by_pin else "P39")
        claimants: dict[str, set[UUID]] = defaultdict(set)
        for person, items in matches.items():
            for item in items:
                claimants[item].add(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Wikidata items for candidates in the 2026 US federal, statewide, and state legislative contests "
                "who have none, where the item states a candidacy in that race (P3602) or its label is their name and "
                "it held the contest's office within a year of 2026; a candidate whose item another person already "
                "holds, or another candidate also claims, is merged into that person"
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
            url="https://www.wikidata.org/wiki/Property:P3602",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in [PINNED_URL, *fetched]:
            cs.artifact(raw.cached(SOURCE, url), source)
        linked = ambiguous = pinned_links = 0
        joined: list[str] = []
        report: list[str] = []
        for person, items in sorted(matches.items()):
            item = next(iter(items))
            if len(items) > 1 or any(len(matches[other]) > 1 for other in claimants[item]):
                ambiguous += 1
                continue
            holder = held.get(item, min(claimants[item]))
            holder = cs.survivors.get(holder, holder)
            label = " | ".join(sorted(text for text, _ in targets[person]["names"]))
            if holder == person:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                linked += 1
                pinned_links += items[item] == "P3602"
                report.append(f"{person}\t{item}\t{items[item]}\t{label}\n")
            elif person not in cs.survivors:
                cs.merge(person, holder)
                joined.append(f"{person}\t{holder}\t{item}\t{items[item]}\t{label}\n")
        (DATA / "logs" / "wikidata_nominees_joined.tsv").write_text("".join(joined), encoding="utf-8")
        (DATA / "logs" / "wikidata_nominees_linked.tsv").write_text("".join(report), encoding="utf-8")
        print(
            f"{len(targets)} candidates without an item, {len(fetched)} of {len(urls)} batches fetched: "
            f"{linked} linked ({pinned_links} by P3602), {ambiguous} ambiguous, "
            f"{len(joined)} merged into the person holding or taking the item"
        )
        return cs.write()
