import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.msp_wikidata"
VERSION = "1"
TITLE = "Members of the Scottish Parliament (P39) on Wikidata"
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": """SELECT ?item ?name ?posLabel ?districtLabel WHERE {
?pos wdt:P279* wd:Q1711695 . ?item p:P39 ?held . ?held ps:P39 ?pos .
OPTIONAL { ?held pq:P768 ?district } OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = "en") }
SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }""",
        "format": "json",
    }
)
TERMS = {"1st": 1999, "2nd": 2003, "3rd": 2007, "4th": 2011, "5th": 2016, "6th": 2021, "7th": 2026}
TITLES = re.compile(r"^(?:(?:mr|ms|mrs|miss|dr|lord|sir|rev|prof|dame|the rt hon|rt hon)\.?\s+)+")
ALIAS = {
    "lothians": "lothian",
    "ballieston": "baillieston",
    "na": "",
    "h": "",
    "eileanan": "western",
    "an": "",
    "iar": "isles",
}
DROPPED = {"islands", "isles", "city", "the", "and", "of"}
UNMATCHED = DATA / "tmp" / "uk-dev" / "msp_unmatched.tsv"


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    plain = TITLES.sub("", re.sub(r"\(.*?\)", "", plain).strip())
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("&", " and ")))


def seat(text: str) -> tuple[str, ...]:
    words = (ALIAS.get(word, word) for word in fold(text).split())
    return tuple(sorted(word for word in words if word and word not in DROPPED))


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")


def emit() -> Path:
    live = "not exists (select from entity_merge m where m.entity_id = x.entity_id)"
    offered: dict[tuple[int, str], set[tuple[str, str]]] = defaultdict(set)
    for binding in json.loads(raw.cached(SOURCE, SERVICE).path.read_text(encoding="utf-8"))["results"]["bindings"]:
        term = re.search(r"the (\d\w\w) Scottish", binding["posLabel"]["value"])
        if term is None:
            continue
        offered[(TERMS[term[1]], fold(binding.get("name", {}).get("value", "")))].add(
            (binding["item"]["value"].rsplit("/", 1)[1], binding.get("districtLabel", {}).get("value", ""))
        )
    with connect() as conn:
        held: dict[str, set[UUID]] = defaultdict(set)
        schemes: dict[UUID, set[str]] = defaultdict(set)
        for person, value in conn.execute(
            f"select x.entity_id, x.value from external_id x join person p on p.id = x.entity_id where x.scheme = 'wikidata' and {live}"
        ).fetchall():
            held[value].add(person)
            schemes[person].add(value)
        members = conn.execute(
            """select e.date_from, ca.person_id, pn.text, an.text
               from candidacy ca join contest_option o on o.id = ca.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id join body b on b.id = c.body_id
               join name bn on bn.entity_id = b.id and bn.is_preferred and bn.text = 'Scottish Parliament'
               join name pn on pn.entity_id = ca.person_id and pn.is_preferred
               join name an on an.entity_id = c.district_area_id and an.is_preferred
               where ca.role = 'list_member' or exists (
                 select 1 from outcome oc join result_set rs on rs.id = oc.result_set_id
                 where oc.option_id = o.id and oc.kind = 'elected' and rs.is_default)"""
        ).fetchall()
        crowd = Counter((day.year, fold(name)) for day, _, name, _ in members)
        found: dict[UUID, set[str]] = defaultdict(set)
        reasons: dict[UUID, list[str]] = defaultdict(list)
        names: dict[UUID, str] = {}
        for day, person, name, district in members:
            key = (day.year, fold(name))
            names[person] = name
            options = offered.get(key, set())
            if not options:
                reasons[person].append(f"{day.year} {district}: no Wikidata MSP of that name in the term")
            elif len({item for item, _ in options}) > 1 or crowd[key] > 1:
                reasons[person].append(f"{day.year} {district}: more than one {name} in the term")
            else:
                item, label = next(iter(options))
                if label and seat(label) != seat(district):
                    reasons[person].append(f"{day.year} {district}: Wikidata gives {label}")
                else:
                    found[person].add(item)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the Scottish Parliament given the Wikidata item whose MSP position matches their name, term, and seat",
            polity="GB",
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
        cs.artifact(raw.cached(SOURCE, SERVICE), source)
        linked = joined = conflicts = 0
        samples = []
        for person, items in sorted(found.items(), key=lambda pair: str(pair[0])):
            if len(items) != 1 or schemes[person] - items:
                conflicts += 1
                reasons[person].append(f"conflicting items {sorted(items | schemes[person])}")
                continue
            item = next(iter(items))
            owners = held.get(item, set()) - {person}
            if len(owners) > 1:
                conflicts += 1
                reasons[person].append(f"{item} is held by {len(owners)} people")
                continue
            if not owners and item not in schemes[person]:
                cs.add("external_id", entity_id=person, scheme="wikidata", value=item)
                held[item].add(person)
                linked += 1
                samples.append((names[person], item, "linked"))
                continue
            if owners:
                owner = next(iter(owners))
                if owner in cs.survivors or person in cs.survivors:
                    conflicts += 1
                    reasons[person].append(f"{item}: merge already staged")
                    continue
                cs.merge(owner, person)
                held[item].add(person)
                joined += 1
                samples.append((names[person], item, "merged into the existing holder"))
        unmatched = [
            (names[person], "; ".join(why))
            for person, why in reasons.items()
            if person not in found or not found[person]
        ]
        UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
        UNMATCHED.write_text("\n".join(f"{name}\t{why}" for name, why in sorted(unmatched)) + "\n", encoding="utf-8")
        print(
            f"{len({p for p in found})} of {len(names)} MSPs matched: {linked} given a Wikidata item, {joined} joined to its holder, "
            f"{conflicts} conflicts, {len(unmatched)} unmatched (listed in {UNMATCHED})"
        )
        for sample in samples[:10]:
            print(sample)
        return cs.write()
