import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, Connection, connect

SOURCE = "id.dpd_match"
VERSION = "1"
TITLE = "Indonesian people on Wikidata, with their labels, aliases, descriptions, and positions held (P39)"
WIKI_TITLE = "Members of the Regional Representative Council by term (Indonesian Wikipedia lists and categories)"
TERMS = {
    2019: (
        "Daftar anggota Dewan Perwakilan Daerah Republik Indonesia 2019–2024",
        "Kategori:Anggota DPD 2019-2024",
        "Kategori:Anggota DPD RI 2019–2024",
    ),
    2024: (
        "Daftar anggota Dewan Perwakilan Daerah Republik Indonesia 2024–2029",
        "Kategori:Anggota DPD 2024–2029",
        "Kategori:Anggota DPD RI 2024–2029",
    ),
}
TITLES = {
    "dr",
    "drs",
    "dra",
    "drg",
    "ir",
    "h",
    "hj",
    "hc",
    "prof",
    "kh",
    "tgk",
    "ust",
    "ustadz",
    "ns",
    "apt",
    "pdt",
    "pr",
    "habib",
    "hi",
    "tgh",
}
DPD = "Q21328635"
QUERY = """SELECT ?name ?item ?description ?pos WHERE {{ VALUES ?name {{ {names} }}
?item rdfs:label|skos:altLabel ?name ; wdt:P31 wd:Q5 ; wdt:P27 wd:Q252 .
OPTIONAL {{ ?item schema:description ?description FILTER(LANG(?description) IN ("id", "en")) }}
OPTIONAL {{ ?item wdt:P39 ?pos }} }}"""
MEMBER = re.compile(r"dewan perwakilan daerah|\bdpd\b|regional representative council|senator", re.IGNORECASE)
UNMATCHED = DATA / "tmp" / "id-dev" / "dpd_unmatched.tsv"


class Run(NamedTuple):
    person: UUID
    day: date
    province: UUID
    printed: str
    core: tuple[str, ...]
    won: bool


def core(printed: str) -> tuple[str, ...]:
    for text in re.sub(r"\(.*?\)", " ", printed).split(","):
        folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
        words = re.findall(r"[a-z]+", re.sub(r"\bk\.\s*h\.", "kh ", folded.replace("'", "").replace("`", "")))
        while words and words[0] in TITLES:
            words.pop(0)
        if words:
            return tuple(words)
    return ()


def runs(conn: Connection) -> list[Run]:
    return [
        Run(person, day, area, printed, core(printed), won)
        for person, day, area, printed, won in conn.execute(
            """select cy.person_id, e.date_from, k.district_area_id, cy.ballot_name,
                      exists (select from outcome w join result_set r on r.id = w.result_set_id
                              where w.option_id = o.id and r.is_default and w.kind = 'elected')
               from candidacy cy
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               join changeset c on c.id = k.changeset_id
               where c.pipeline = 'id.kpu_dpd' and c.status = 'published'
                 and not exists (select from entity_merge m where m.entity_id = cy.person_id)"""
        ).fetchall()
    ]


def label(words: tuple[str, ...]) -> str:
    return " ".join(word.capitalize() for word in words)


def urls(conn: Connection) -> list[str]:
    names = sorted({label(run.core) for run in runs(conn) if run.won})
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": QUERY.format(
                    names=" ".join(
                        json.dumps(name, ensure_ascii=False) + "@" + lang
                        for name in names[start : start + 60]
                        for lang in ("id", "en")
                    )
                ),
                "format": "json",
            }
        )
        for start in range(0, len(names), 60)
    ]


def wiki(title: str) -> str:
    walk = (
        {"generator": "categorymembers", "gcmtitle": title, "gcmnamespace": 0, "gcmlimit": 500}
        if title.startswith("Kategori:")
        else {"generator": "links", "titles": title, "gplnamespace": 0, "gpllimit": 500, "redirects": 1}
    )
    return "https://id.wikipedia.org/w/api.php?" + urlencode(
        {"action": "query", **walk, "prop": "pageprops", "ppprop": "wikibase_item", "format": "json"}
    )


def listing() -> dict[int, dict[tuple[str, ...], set[str]]]:
    listed: dict[int, dict[tuple[str, ...], set[str]]] = defaultdict(lambda: defaultdict(set))
    for year, titles in TERMS.items():
        for title in titles:
            found = json.loads(raw.cached(SOURCE, wiki(title)).path.read_text(encoding="utf-8"))["query"]
            items = {
                page["title"]: page["pageprops"]["wikibase_item"]
                for page in found.get("pages", {}).values()
                if "wikibase_item" in page.get("pageprops", {})
            }
            for hop in found.get("redirects", []):
                if hop["to"] in items:
                    items[hop["from"]] = items[hop["to"]]
            for name, item in items.items():
                listed[year][plain(core(name))].add(item)
    return listed


def humans(listed: dict[int, dict[tuple[str, ...], set[str]]]) -> str:
    items = sorted({item for names in listed.values() for found in names.values() for item in found})
    return "https://query.wikidata.org/sparql?" + urlencode(
        {
            "query": f"SELECT ?item WHERE {{ VALUES ?item {{ {' '.join(f'wd:{item}' for item in items)} }} ?item wdt:P31 wd:Q5 }}",
            "format": "json",
        }
    )


def fetch() -> None:
    with connect() as conn:
        for url in urls(conn):
            raw.fetch(SOURCE, url, suffix=".json")
    for titles in TERMS.values():
        for title in titles:
            raw.fetch(SOURCE, wiki(title), suffix=".json")
    raw.fetch(SOURCE, humans(listing()), suffix=".json")


def plain(words: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(word for word in words if len(word) > 1)


def initialed(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return (
        len(a) == len(b)
        and len([x for x, y in zip(a, b) if x == y and len(x) > 2]) > 1
        and all(x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y)) for x, y in zip(a, b))
    )


def papuan(names: dict[UUID, str], a: Run, b: Run) -> bool:
    return names[a.province].startswith("Papua") and names[b.province].startswith("Papua")


def groups(conn: Connection, every: list[Run]) -> dict[UUID, list[UUID]]:
    names: dict[UUID, str] = dict(
        conn.execute(
            "select entity_id, text from name where is_preferred and entity_id = any(%s)",
            (list({run.province for run in every}),),
        ).fetchall()
    )
    days = sorted({run.day for run in every})
    crowd = Counter((run.day, plain(run.core)) for run in every)
    rules: dict[str, list[tuple[Run, Run]]] = {"province": [], "papua": [], "variant": [], "moved": []}
    for a in every:
        for b in every:
            if not a.day < b.day or not plain(a.core) or not plain(b.core):
                continue
            same = plain(a.core) == plain(b.core)
            near = a.province == b.province or papuan(names, a, b)
            if same and a.province == b.province:
                rules["province"].append((a, b))
            elif same and papuan(names, a, b):
                rules["papua"].append((a, b))
            elif near and (
                initialed(a.core, b.core)
                or (
                    a.won
                    and days.index(b.day) == days.index(a.day) + 1
                    and plain(b.core)[: len(plain(a.core))] == plain(a.core)
                )
            ):
                rules["variant"].append((a, b))
            elif (
                same
                and len([word for word in plain(a.core) if len(word) > 2]) > 1
                and crowd[a.day, plain(a.core)] == crowd[b.day, plain(b.core)] == 1
            ):
                rules["moved"].append((a, b))

    parent: dict[UUID, UUID] = {}
    held: dict[UUID, set[date]] = {run.person: {run.day} for run in every}

    def root(node: UUID) -> UUID:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for rule, pairs in rules.items():
        sides = Counter(side for a, b in pairs for side in ((a.person, b.day), (b.person, a.day)))
        matched = clashes = 0
        for a, b in sorted(pairs, key=lambda pair: (pair[0].day, pair[1].day)):
            if sides[a.person, b.day] != 1 or sides[b.person, a.day] != 1:
                continue
            left, right = root(a.person), root(b.person)
            if left == right:
                continue
            if held[left] & held[right]:
                clashes += 1
                continue
            parent[right] = left
            held[left] |= held.pop(right)
            matched += 1
            if rule != "province":
                print(
                    f"  {rule}: {a.printed} ({names[a.province]}, {a.day.year}) = {b.printed} ({names[b.province]}, {b.day.year})"
                )
        print(f"{rule}: {len(pairs)} pairs, {matched} joined, {clashes} refused for running twice on one day")

    found: dict[UUID, list[UUID]] = defaultdict(list)
    for node in list(parent):
        found[root(node)].append(node)
    return {key: people for key, people in found.items() if len(people) > 1}


def offers() -> tuple[
    dict[tuple[str, ...], dict[str, set[str]]], dict[int, dict[tuple[str, ...], set[str]]], list[str]
]:
    with connect() as conn:
        queries = urls(conn)
    labeled: dict[tuple[str, ...], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for url in queries:
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            item = binding["item"]["value"].rsplit("/", 1)[1]
            evidence = labeled[plain(core(binding["name"]["value"]))][item]
            if binding.get("pos", {}).get("value", "").endswith(f"/{DPD}"):
                evidence.add("held")
            if MEMBER.search(binding.get("description", {}).get("value", "")):
                evidence.add("described")
    listed = listing()
    check = humans(listed)
    people = {
        binding["item"]["value"].rsplit("/", 1)[1]
        for binding in json.loads(raw.cached(SOURCE, check).path.read_text(encoding="utf-8"))["results"]["bindings"]
    }
    for names in listed.values():
        for name in list(names):
            names[name] &= people
            if not names[name]:
                del names[name]
    pages = [wiki(title) for titles in TERMS.values() for title in titles]
    return labeled, listed, [*queries, check, *pages]


def emit() -> Path:
    labeled, listed, fetched = offers()
    with connect() as conn:
        every = runs(conn)
        merged = groups(conn, every)
        survivor = {
            person: min(people, key=lambda member: next(run.day for run in every if run.person == member))
            for people in merged.values()
            for person in people
        }
        winners = [run for run in every if run.won]
        crowd = Counter((run.day, plain(run.core)) for run in winners)
        claims: dict[UUID, set[str]] = defaultdict(set)
        reasons: dict[UUID, list[str]] = defaultdict(list)
        for run in winners:
            key = plain(run.core)
            items = set(listed[run.day.year].get(key, set())) | {
                item for item, evidence in labeled.get(key, {}).items() if evidence
            }
            if not items:
                items = {
                    item
                    for name, found in listed[run.day.year].items()
                    if len(name) > 1
                    and set(name) <= set(key)
                    and [set(name) <= set(plain(other.core)) for other in winners if other.day == run.day].count(True)
                    == 1
                    for item in found
                }
            if not items:
                reasons[run.person].append(
                    f"{run.day.year} {run.printed}: no Wikidata item of that name listed or described as a member"
                )
            elif len(items) > 1 or crowd[run.day, key] > 1:
                reasons[run.person].append(f"{run.day.year} {run.printed}: {len(items)} items {sorted(items)}")
            else:
                claims[survivor.get(run.person, run.person)] |= items
        held: dict[str, set[UUID]] = defaultdict(set)
        owned: dict[UUID, set[str]] = defaultdict(set)
        for person, value in conn.execute(
            """select x.entity_id, x.value from external_id x where x.scheme = 'wikidata'
               and not exists (select from entity_merge m where m.entity_id = x.entity_id)"""
        ).fetchall():
            held[value].add(person)
            owned[person].add(value)
        spans: dict[UUID, set[date]] = defaultdict(set)
        for run in every:
            spans[survivor.get(run.person, run.person)].add(run.day)
        rivals: dict[str, list[UUID]] = defaultdict(list)
        for person, items in claims.items():
            if len(items) == 1:
                rivals[next(iter(items))].append(person)
        joint: list[list[UUID]] = []
        for item, people in rivals.items():
            if len(people) > 1 and sum(len(spans[person]) for person in people) == len(
                set().union(*(spans[person] for person in people))
            ):
                people.sort(key=lambda person: min(spans[person]))
                joint.append(people)
                for person in people[1:]:
                    del claims[person]
        takers = Counter(item for items in claims.values() for item in items)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Regional Representative Council (DPD) candidates of 2019 and 2024 matched across the two elections by "
                "printed name without titles, degrees, or initials: in the same province, in a Papuan province split "
                "from the 2019 one, as a sitting member under a lengthened name in the same province, or with a "
                "name of two or more words that only one candidate bore in each election, or with initials for given "
                "names in the same province; members elected given "
                "the Wikidata item of their name, or of two or more words of it that no other member of the term "
                "bears, that Indonesian Wikipedia lists or categorizes as a member for that term, or that holds "
                "the DPD membership (P39) or is described as a DPD member; and members elected in both years under "
                "different names joined when they take the same item"
            ),
            polity="ID",
            kind="match",
        )
        for publisher, title, url, kind, license, license_url in (
            (
                "Wikidata",
                TITLE,
                "https://www.wikidata.org/wiki/Property:P39",
                "wikidata",
                "CC0 1.0",
                "https://creativecommons.org/publicdomain/zero/1.0/",
            ),
            (
                "Wikipedia",
                WIKI_TITLE,
                "https://id.wikipedia.org/wiki/Kategori:Anggota_DPD_RI_2024–2029",
                "community",
                "CC BY-SA 4.0",
                "https://creativecommons.org/licenses/by-sa/4.0/",
            ),
        ):
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind=kind,
                    publisher=publisher,
                    title=title,
                    url=url,
                    license=license,
                    license_url=license_url,
                )
            for page in fetched:
                if ("wikidata.org" in page) == (publisher == "Wikidata"):
                    cs.artifact(raw.cached(SOURCE, page), source)
        for people in merged.values():
            keep = survivor[people[0]]
            for person in people:
                if person != keep:
                    cs.merge(person, keep)
        for people in joint:
            for person in people[1:]:
                cs.merge(person, people[0])
                print(f"  same item in both terms: {person} into {people[0]}")
        sizes = Counter(len(people) for people in merged.values())
        linked = joined = conflicts = 0
        for person, items in sorted(claims.items(), key=lambda pair: str(pair[0])):
            item = next(iter(items))
            owners = held.get(item, set()) - {person}
            if len(items) > 1 or takers[item] > 1 or owned[person] - items or len(owners) > 1:
                conflicts += 1
                reasons[person].append(f"conflicting items {sorted(items | owned[person])}, held by {len(owners)}")
                continue
            if owners:
                cs.merge(person, next(iter(owners)))
                joined += 1
            elif item not in owned[person]:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                linked += 1
        unmatched = sorted(why for person, notes in reasons.items() for why in notes)
        UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
        UNMATCHED.write_text("".join(f"{why}\n" for why in unmatched), encoding="utf-8")
        print(
            f"{sum(sizes.values())} people from {sum(n * c for n, c in sizes.items())} candidacies; "
            f"{len(joint)} more joined as one item's member in both terms; "
            f"{len(winners)} winners: {linked} given a Wikidata item, {joined} merged into its holder, "
            f"{conflicts} conflicts, {len(unmatched)} unmatched (listed in {UNMATCHED})"
        )
        return cs.write()
