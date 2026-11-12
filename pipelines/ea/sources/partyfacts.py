import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "partyfacts"
VERSION = "1"
PUBLISHER = "Party Facts (Döring and Regel)"
TITLE = "Party Facts: core and external parties, 2025 release"
LANDING = "https://partyfacts.herokuapp.com/"
DATAVERSE = "https://doi.org/10.7910/DVN/GM8LWQ"
CORE = "https://partyfacts.herokuapp.com/download/core-parties-csv/"
EXTERNAL = "https://partyfacts.herokuapp.com/download/external-parties-csv/"
WIKIDATA = "Wikidata"
WIKIDATA_TITLE = "Wikidata items of the Wikipedia pages that Party Facts cites"
BATCH = 50
ATTRIBUTION = "Döring, Holger, and Sven Regel. 2019. Party Facts: A database of political parties worldwide. Party Politics 25(2): 97-109."
COUNTRIES = {
    "AT": "AUT",
    "BE": "BEL",
    "BG": "BGR",
    "BR": "BRA",
    "CA": "CAN",
    "CH": "CHE",
    "CY": "CYP",
    "CZ": "CZE",
    "DE": "DEU",
    "DK": "DNK",
    "EE": "EST",
    "ES": "ESP",
    "FI": "FIN",
    "FR": "FRA",
    "GB": "GBR",
    "GR": "GRC",
    "HR": "HRV",
    "HU": "HUN",
    "IE": "IRL",
    "IT": "ITA",
    "LT": "LTU",
    "LU": "LUX",
    "LV": "LVA",
    "MT": "MLT",
    "NL": "NLD",
    "PL": "POL",
    "PT": "PRT",
    "RO": "ROU",
    "SE": "SWE",
    "SI": "SVN",
    "SK": "SVK",
    "TR": "TUR",
    "US": "USA",
}
ISO2 = {iso3: iso2 for iso2, iso3 in COUNTRIES.items()}
NATIONAL = DATA / "tmp"


def _rows(artifact: raw.Artifact) -> list[dict[str, str]]:
    with artifact.path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _page(link: str) -> tuple[str, str] | None:
    parsed = urlparse(link)
    host = parsed.netloc.split(".")
    if len(host) < 3 or host[-2] != "wikipedia" or not parsed.path.startswith("/wiki/"):
        return None
    return host[0] + "wiki", unquote(parsed.path[len("/wiki/") :]).replace("_", " ")


def _pages(core: list[dict[str, str]]) -> dict[str, list[str]]:
    pages: dict[str, set[str]] = defaultdict(set)
    for row in core:
        if row["country"] in ISO2 and (page := _page(row["wikipedia"])):
            pages[page[0]].add(page[1])
    return {site: sorted(titles) for site, titles in pages.items()}


def _urls(core: list[dict[str, str]]) -> list[tuple[str, list[str], str]]:
    urls = []
    for site, titles in sorted(_pages(core).items()):
        for start in range(0, len(titles), BATCH):
            chunk = titles[start : start + BATCH]
            joined = quote("|".join(chunk), safe="")
            urls.append(
                (
                    site,
                    chunk,
                    f"https://{site.removesuffix('wiki').replace('_', '-')}.wikipedia.org/w/api.php?action=query&prop=pageprops&ppprop=wikibase_item&titles={joined}&redirects=1&format=json",
                )
            )
    return urls


def fetch() -> None:
    core = _rows(raw.fetch(SOURCE, CORE, suffix=".csv"))
    raw.fetch(SOURCE, EXTERNAL, suffix=".csv")
    held = raw.keys(SOURCE)
    for _, _, url in _urls(core):
        if url not in held:
            raw.fetch(SOURCE, url, suffix=".json")
            time.sleep(1)


def _qids(core: list[dict[str, str]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for site, titles, url in _urls(core):
        query = json.loads(raw.cached(SOURCE, url).path.read_text())["query"]
        moved = {item["from"]: item["to"] for item in [*query.get("normalized", []), *query.get("redirects", [])]}
        landed = {
            page["title"]: page["pageprops"]["wikibase_item"]
            for page in query["pages"].values()
            if "wikibase_item" in page.get("pageprops", {})
        }
        for title in titles:
            final = title
            while final in moved:
                final = moved[final]
            if final in landed:
                found[f"{site}:{title}"] = landed[final]
    return found


def emit() -> Path:
    core = [row for row in _rows(raw.cached(SOURCE, CORE)) if row["country"] in ISO2]
    external = _rows(raw.cached(SOURCE, EXTERNAL))
    qids = _qids(core)
    parlgov: dict[str, set[str]] = defaultdict(set)
    for row in external:
        if row["dataset_key"] == "parlgov" and row["country"] in ISO2:
            parlgov[row["dataset_party_id"]].add(row["partyfacts_id"])
    routes: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in core:
        page = _page(row["wikipedia"])
        if page and (item := qids.get(f"{page[0]}:{page[1]}")):
            routes[row["partyfacts_id"]].append(("wikidata", item, row["country"]))
    with connect() as conn:
        by_item = {
            value: (entity, path)
            for entity, value, path in conn.execute(
                """select x.entity_id, x.value, coalesce(split_part(s.path, '/', 1), '') from external_id x
                   join party p on p.id = x.entity_id left join slug s on s.entity_id = p.polity_id and s.is_current
                   where x.scheme = 'wikidata' and not exists (select from entity_merge m where m.entity_id = p.id)"""
            )
        }
        by_parlgov = {
            value: (entity, path)
            for entity, value, path in conn.execute(
                """select x.entity_id, x.value, coalesce(split_part(s.path, '/', 1), '') from external_id x
                   join party p on p.id = x.entity_id left join slug s on s.entity_id = p.polity_id and s.is_current
                   where x.scheme = 'parlgov' and not exists (select from entity_merge m where m.entity_id = p.id)"""
            )
        }
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'partyfacts'")}
        candidates: dict[str, dict[UUID, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
        for pf, found in routes.items():
            for route, item, country in found:
                hit = by_item.get(item)
                if hit and hit[1].upper() in ("", ISO2[country]):
                    candidates[pf][hit[0]].add((route, item))
        for value, ids in parlgov.items():
            hit = by_parlgov.get(value)
            if hit:
                for pf in ids:
                    candidates[pf][hit[0]].add(("parlgov", value))
        pf_of: dict[UUID, set[str]] = defaultdict(set)
        for pf, entities in candidates.items():
            if len(entities) == 1:
                pf_of[next(iter(entities))].add(pf)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Party Facts IDs for parties, matched through Wikipedia-page Wikidata items and ParlGov IDs",
            polity=None,
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            attribution=ATTRIBUTION,
            notes=f"Dataverse record {DATAVERSE} is released under CC0 1.0.",
        )
        cs.artifact(raw.cached(SOURCE, CORE), source)
        cs.artifact(raw.cached(SOURCE, EXTERNAL), source)
        wikidata = cs.source(WIKIDATA, WIKIDATA_TITLE)
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher=WIKIDATA,
            title=WIKIDATA_TITLE,
            url="https://www.wikidata.org",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for _, _, url in _urls(core):
            cs.artifact(raw.cached(SOURCE, url), wikidata)
        linked = {entity for entity, _ in by_item.values()}
        filled: list[tuple[str, str, str]] = []
        skipped = []
        report = []
        for pf, entities in candidates.items():
            if len(entities) != 1:
                skipped.append((pf, "several parties", ",".join(str(e) for e in entities)))
                continue
            entity, how = next(iter(entities.items()))
            if len(pf_of[entity]) != 1:
                skipped.append((pf, "party has several Party Facts IDs", str(entity)))
                continue
            if pf in known:
                continue
            cs.add("external_id", entity_id=entity, scheme="partyfacts", value=pf)
            for route, key in sorted(how):
                cs.add(
                    "citation",
                    entity_id=entity,
                    source_id=wikidata if route == "wikidata" else source,
                    locator={"partyfacts_id": pf, "route": route, "key": key},
                )
            report.append((pf, str(entity), "+".join(sorted({r for r, _ in how}))))
            if entity not in linked and "wikidata" not in {r for r, _ in how}:
                items = {item for _, item, _ in routes.get(pf, ())}
                if len(items) == 1 and (item := next(iter(items))) not in by_item:
                    cs.add("external_id", entity_id=entity, scheme="wikidata", value=item)
                    cs.add(
                        "citation",
                        entity_id=entity,
                        source_id=wikidata,
                        locator={"partyfacts_id": pf, "route": "partyfacts", "key": item},
                    )
                    filled.append((pf, str(entity), item))
        NATIONAL.mkdir(parents=True, exist_ok=True)
        with (NATIONAL / "partyfacts_matches.tsv").open("w") as out:
            out.write("partyfacts_id\tparty\troute\n")
            out.writelines("\t".join(row) + "\n" for row in report)
        with (NATIONAL / "partyfacts_filled.tsv").open("w") as out:
            out.write("partyfacts_id\tparty\twikidata\n")
            out.writelines("\t".join(row) + "\n" for row in filled)
        with (NATIONAL / "partyfacts_skipped.tsv").open("w") as out:
            out.write("partyfacts_id\treason\tparties\n")
            out.writelines("\t".join(row) + "\n" for row in skipped)
        return cs.write()
