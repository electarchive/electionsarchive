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

SOURCE = "gb.offices"
VERSION = "1"
UNION = date(1801, 1, 1)
PAST = "https://www.gov.uk/government/history/past-prime-ministers"
PAST_TITLE = "Past Prime Ministers"
WIKIDATA_TITLE = "Prime Ministers (P39 Q14211) and Speakers of the House of Commons (P39 Q464103)"
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"


def query(position: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {
            "query": f"""SELECT ?item ?name ?start ?end ?cause WHERE {{ ?item p:P39 ?held . ?held ps:P39 wd:{position} .
OPTIONAL {{ ?held pq:P580 ?start }} OPTIONAL {{ ?held pq:P582 ?end }} OPTIONAL {{ ?held pq:P1534 ?cause }}
OPTIONAL {{ ?item rdfs:label ?name FILTER(LANG(?name) = "en") }} }}""",
            "format": "json",
        }
    )


SPEAKERS = query("Q464103")
PRIME_MINISTERS = query("Q14211")


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def terms(url: str) -> list[tuple[str, str, date, date | None, str | None]]:
    held = []
    for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
        start = binding.get("start", {}).get("value", "")[:10]
        if not start[:1].isdigit():
            continue
        end = binding.get("end", {}).get("value", "")[:10]
        item = binding["item"]["value"].rsplit("/", 1)[1]
        held.append(
            (
                item,
                binding.get("name", {}).get("value", item),
                date.fromisoformat(start),
                date.fromisoformat(end) if end[:1].isdigit() else None,
                binding.get("cause", {}).get("value", "").rsplit("/", 1)[-1] or None,
            )
        )
    return sorted(set(held), key=lambda term: (term[2], term[0]))


def fetch() -> None:
    raw.fetch(SOURCE, PAST, suffix=".html")
    raw.fetch(SOURCE, SPEAKERS, suffix=".json")
    raw.fetch(SOURCE, PRIME_MINISTERS, suffix=".json")


def emit() -> Path:
    past = raw.cached(SOURCE, PAST)
    listed: list[tuple[str, int, int]] = []
    for card in past.path.read_text(encoding="utf-8").split('<h3 class="gem-c-image-card__title')[1:]:
        head, _, rest = card.partition("</h3>")
        who = " ".join(re.sub(r"<[^>]+>", " ", head.partition(">")[2]).split())
        for first, last in re.findall(r"(\d{4}) to (\d{4})", rest.partition("</ul>")[0]):
            listed.append((who, int(first), int(last)))
    wikidata = terms(PRIME_MINISTERS)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Prime Ministers since 1721, from gov.uk, and Speakers of the House of Commons since 1801, "
            "from Wikidata",
            polity="GB",
        )
        gov = cs.source("Prime Minister's Office, 10 Downing Street", PAST_TITLE)
        cs.add(
            "source",
            id=gov,
            kind="official_body",
            publisher="Prime Minister's Office, 10 Downing Street",
            title=PAST_TITLE,
            url=PAST,
            license="Open Government Licence v3.0",
            license_url=OGL,
            notes="Gives each term in years; the day of each term comes from Wikidata where its years agree.",
        )
        cs.artifact(past, gov)
        source = cs.source("Wikidata", WIKIDATA_TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=WIKIDATA_TITLE,
            url="https://www.wikidata.org/wiki/Q14211",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, PRIME_MINISTERS), source)
        cs.artifact(raw.cached(SOURCE, SPEAKERS), source)
        commons = cs.key("gb.body", "house-of-commons")
        if commons in cs.minted:
            raise ValueError("the House of Commons must already exist")
        gb = cs.polity("GB")
        offices = {}
        for code, kind, body, label in (
            ("prime-minister", "head_of_government", None, "Prime Minister of the United Kingdom"),
            ("speaker", "speaker", commons, "Speaker of the House of Commons"),
        ):
            offices[code] = cs.key("gb.office", code)
            if offices[code] in cs.minted:
                cs.add("office", id=offices[code], polity_id=gb, body_id=body, kind=kind)
                cs.add("name", entity_id=offices[code], text=label, lang="en", kind="official", is_preferred=True)

        def person(item: str, label: str) -> UUID:
            id = cs.key("wikidata", item)
            if id in cs.minted:
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=label, lang="en", kind="official", is_preferred=True)
            return id

        dated = 0
        for who, first, last in listed:
            found = [
                term for term in wikidata if term[2].year == first and term[3] is not None and term[3].year == last
            ]
            items = {term[0] for term in found}
            if len(items) > 1:
                found = [term for term in found if words(term[1]) & words(who)]
                items = {term[0] for term in found}
            if len(items) != 1:
                raise ValueError(f"{who} {first}–{last}: Wikidata gives {sorted(items) or 'no one'}")
            item = items.pop()
            spans = {(term[2], term[3]) for term in found}
            if len(spans) == 1:
                (start, end), precision = spans.pop(), "day"
                dated += 1
            else:
                start, end, precision = date(first, 1, 1), date(last, 1, 1), "year"
            label = next(term[1] for term in found)
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"prime-minister:{item}:{first}"),
                person_id=person(item, re.sub(r"^The Rt Hon ", "", who) if label == item else label),
                office_id=offices["prime-minister"],
                from_date=start,
                from_precision=precision,
                to_date=end,
                to_precision=precision,
                acquired_via="appointed",
            )
        speakers = 0
        seen: set[tuple[str, date]] = set()
        for item, label, start, stop, cause in terms(SPEAKERS):
            if (stop is not None and stop < UNION) or (item, start) in seen:
                continue
            seen.add((item, start))
            speakers += 1
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"speaker:{item}:{start}"),
                person_id=person(item, label),
                office_id=offices["speaker"],
                body_id=commons,
                from_date=start,
                from_precision="day",
                to_date=stop,
                to_precision="day",
                acquired_via="elected",
                ended_via=ENDED.get(cause or "") if stop is not None else None,
            )
        print(f"{len(listed)} Prime Minister terms ({dated} dated to the day by Wikidata), {speakers} Speaker terms")
        return cs.write()
