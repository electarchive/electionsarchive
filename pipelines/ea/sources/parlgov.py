import json
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "parlgov"
VERSION = "1"
DATABASE = "https://dataverse.harvard.edu/api/access/datafile/10437086"
LANDING = "https://doi.org/10.7910/DVN/2VZ5ZC"
PUBLISHER = "ParlGov (Döring, Huber, and Manow)"
TITLE = "ParlGov 2024 Release: cabinets, parties, and elections"
HEADS = "Heads of government"
COUNTRIES = {"DEU": "DE", "TUR": "TR", "CAN": "CA", "ITA": "IT", "GBR": "GB", "FRA": "FR"}
POSITIONS = {
    "Q4970706": ("DE", "chancellor"),
    "Q56022": ("DE", "chancellor"),
    "Q29034484": ("DE", "chancellor"),
    "Q7050658": ("DE", "chancellor"),
    "Q1587677": ("FR", "prime-minister"),
    "Q3409212": ("FR", "prime-minister"),
    "Q23004974": ("FR", "provisional-government"),
    "Q796897": ("IT", "prime-minister"),
    "Q14211": ("GB", "prime-minister"),
    "Q839078": ("CA", "prime-minister"),
    "Q2430415": ("TR", "prime-minister"),
    "Q1922067": ("TR", "president"),
}
OFFICES = {
    ("DE", "chancellor"): "Chancellor of Germany",
    ("FR", "prime-minister"): "Prime Minister of France",
    ("FR", "provisional-government"): "President of the Provisional Government of the French Republic",
    ("IT", "prime-minister"): "Prime Minister of Italy",
    ("GB", "prime-minister"): "Prime Minister of the United Kingdom",
    ("CA", "prime-minister"): "Prime Minister of Canada",
    ("TR", "prime-minister"): "Prime Minister of Turkey",
}
HOLDERS = """SELECT ?pos ?holder ?start ?end ?name WHERE {{
  VALUES ?pos {{ {positions} }}
  ?holder p:P39 ?statement . ?statement ps:P39 ?pos ; pq:P580 ?start .
  OPTIONAL {{ ?statement pq:P582 ?end }}
  OPTIONAL {{ ?holder rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?holder rdfs:label ?mul FILTER(LANG(?mul) = "mul") }}
  OPTIONAL {{ ?holder rdfs:label ?native FILTER(LANG(?native) IN ("de", "fr", "it", "tr")) }}
  BIND(COALESCE(?en, ?mul, ?native) AS ?name)
}}"""
SPLITS: dict[int, list[tuple[str, date | None, date | None]]] = {
    1436: [("Q47750", None, date(2017, 12, 14)), ("Q85776185", date(2017, 12, 14), None)],
    1727: [
        ("Q49762", None, None),
        ("Q49763", None, None),
    ],
    596: [
        ("Q215350", None, date(2009, 3, 29)),
        ("Q47720", date(2009, 3, 29), date(2013, 11, 16)),
        ("Q14924303", date(2013, 11, 16), None),
    ],
    658: [
        ("Q173152", None, date(2015, 5, 30)),
        ("Q20012759", date(2015, 5, 30), None),
    ],
    509: [
        ("Q827415", None, date(2007, 12, 1)),
        ("Q587370", date(2007, 12, 1), None),
    ],
    507: [
        ("Q929507", None, date(1995, 2, 18)),
        ("Q19079", date(1995, 2, 18), None),
    ],
}
UNLINKED = {1094}
ENDS = {"Hitler": date(1945, 4, 30), "Petain": date(1940, 7, 11)}
RECENT: list[tuple[str, str, date, date | None, str, list[str]]] = [
    ("FR", "Attal", date(2024, 1, 9), date(2022, 6, 19), "Q23731823", ["Q587370", "Q108846587"]),
    (
        "FR",
        "Barnier",
        date(2024, 9, 21),
        date(2024, 7, 7),
        "Q20012759",
        ["Q23731823", "Q587370", "Q108846587", "Q82892"],
    ),
    (
        "FR",
        "Bayrou",
        date(2024, 12, 23),
        date(2024, 7, 7),
        "Q587370",
        ["Q23731823", "Q20012759", "Q108846587", "Q82892"],
    ),
    (
        "FR",
        "Lecornu I",
        date(2025, 10, 5),
        date(2024, 7, 7),
        "Q23731823",
        ["Q20012759", "Q587370", "Q108846587", "Q82892"],
    ),
    ("FR", "Lecornu II", date(2025, 10, 12), date(2024, 7, 7), "Q23731823", ["Q587370", "Q108846587"]),
    ("GB", "Starmer", date(2024, 7, 5), date(2024, 7, 4), "Q9630", []),
    ("GB", "Burnham", date(2026, 7, 20), date(2024, 7, 4), "Q9630", []),
    ("CA", "Carney I", date(2025, 3, 14), date(2021, 9, 20), "Q138345", []),
    ("CA", "Carney II", date(2025, 4, 28), date(2025, 4, 28), "Q138345", []),
    ("DE", "Merz", date(2025, 5, 6), date(2025, 2, 23), "Q49762", ["Q49763", "Q49768"]),
]


def _holders_url() -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {"query": HOLDERS.format(positions=" ".join(f"wd:{item}" for item in POSITIONS)), "format": "json"}
    )


def _database() -> sqlite3.Connection:
    db = sqlite3.connect(raw.cached(SOURCE, DATABASE).path)
    db.row_factory = sqlite3.Row
    return db


def _articles(db: sqlite3.Connection) -> dict[str, list[tuple[int, str]]]:
    hosts: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in db.execute(
        f"""select distinct p.id, p.wikipedia from cabinet_party cp join cabinet c on c.id = cp.cabinet_id
            join country k on k.id = c.country_id join party p on p.id = cp.party_id
            where k.name_short in ({",".join("?" * len(COUNTRIES))}) and p.wikipedia like 'http%'
            order by p.id""",
        list(COUNTRIES),
    ):
        link = urlparse(row["wikipedia"].strip())
        hosts[link.netloc].append((row["id"], unquote(link.path.removeprefix("/wiki/")).replace("_", " ")))
    return hosts


def _lookups(db: sqlite3.Connection) -> list[tuple[str, list[tuple[int, str]]]]:
    return [
        (
            f"https://{host}/w/api.php?"
            + urlencode(
                {
                    "action": "query",
                    "prop": "pageprops",
                    "ppprop": "wikibase_item",
                    "redirects": 1,
                    "format": "json",
                    "titles": "|".join(title for _, title in items[start : start + 50]),
                }
            ),
            items[start : start + 50],
        )
        for host, items in sorted(_articles(db).items())
        for start in range(0, len(items), 50)
    ]


def _parties(db: sqlite3.Connection) -> dict[int, str]:
    parties: dict[int, str] = {}
    for url, items in _lookups(db):
        query = json.loads(raw.cached(SOURCE, url).path.read_text())["query"]
        renamed = {entry["from"]: entry["to"] for entry in query.get("normalized", []) + query.get("redirects", [])}
        pages = {page["title"]: page.get("pageprops", {}).get("wikibase_item") for page in query["pages"].values()}
        for party, title in items:
            while title in renamed:
                title = renamed[title]
            if pages.get(title) is not None:
                parties[party] = pages[title]
    return parties


def _labels(db: sqlite3.Connection) -> list[str]:
    items = sorted(
        {
            *_parties(db).values(),
            *(item for splits in SPLITS.values() for item, _, _ in splits),
            *(item for recent in RECENT for item in (recent[4], *recent[5])),
        }
    )
    return [
        "https://www.wikidata.org/w/api.php?"
        + urlencode(
            {
                "action": "wbgetentities",
                "props": "labels",
                "languages": "en|mul",
                "format": "json",
                "ids": "|".join(items[start : start + 50]),
            }
        )
        for start in range(0, len(items), 50)
    ]


def fetch() -> None:
    raw.fetch(SOURCE, DATABASE, suffix=".db")
    raw.fetch(SOURCE, _holders_url(), suffix=".json")
    db = _database()
    for url, _ in _lookups(db):
        raw.fetch(SOURCE, url, suffix=".json")
    for url in _labels(db):
        raw.fetch(SOURCE, url, suffix=".json")


def _folds(text: str) -> set[str]:
    text = text.lower().replace("ı", "i").replace("-", " ").replace(".", "")
    umlauts = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return {
        " ".join("".join(c for c in unicodedata.normalize("NFKD", variant) if not unicodedata.combining(c)).split())
        for variant in (text, umlauts)
    }


def emit() -> Path:
    db = _database()
    parties = _parties(db)
    labels = {
        item: next((entity["labels"][lang]["value"] for lang in ("en", "mul") if lang in entity["labels"]), None)
        for url in _labels(db)
        for item, entity in json.loads(raw.cached(SOURCE, url).path.read_text())["entities"].items()
    }
    holders: dict[str, list[tuple[str, str, date, date | None, str]]] = defaultdict(list)
    for binding in json.loads(raw.cached(SOURCE, _holders_url()).path.read_text())["results"]["bindings"]:
        if not binding["start"]["value"][:1].isdigit():
            continue
        iso, office = POSITIONS[binding["pos"]["value"].rsplit("/", 1)[1]]
        holders[iso].append(
            (
                binding["holder"]["value"].rsplit("/", 1)[1],
                office,
                date.fromisoformat(binding["start"]["value"][:10]),
                date.fromisoformat(binding["end"]["value"][:10]) if "end" in binding else None,
                binding.get("name", {}).get("value", ""),
            )
        )

    def head(iso: str, cabinet: str, day: date) -> tuple[str, str, str] | None:
        surname = " ".join(
            word for word in cabinet.split() if len(word) > 1 and word.strip("IVX") != "" and word != "Cabinet"
        )
        found = sorted(
            {
                (start, holder, office, name)
                for holder, office, start, end, name in holders[iso]
                if start <= day + timedelta(days=14)
                and (end is None or end >= day)
                and any(f" {variant} " in f" {folded} " for variant in _folds(surname) for folded in _folds(name))
            }
        )
        return None if not found else found[-1][1:]

    countries = {row["name_short"]: row["id"] for row in db.execute("select id, name_short from country")}
    cabinets = [
        dict(row)
        for row in db.execute(
            f"""select c.*, k.name_short as country, e.date as election_date from cabinet c
                join country k on k.id = c.country_id
                left join election e on e.id = c.previous_parliament_election_id
                where k.name_short in ({",".join("?" * len(COUNTRIES))}) order by c.country_id, c.start_date""",
            list(COUNTRIES),
        )
    ]
    members: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in db.execute(
        """select cp.cabinet_id, cp.party_id, cp.pm, v.seats, p.name_short, p.name, p.name_english
           from cabinet_party cp join party p on p.id = cp.party_id
           left join view_cabinet v on v.cabinet_id = cp.cabinet_id and v.party_id = cp.party_id
           where cp.cabinet_id in (select c.id from cabinet c where c.country_id in ({}))""".format(
            ",".join(str(countries[code]) for code in COUNTRIES)
        )
    ):
        members[row["cabinet_id"]].append(row)

    with connect() as conn:
        parlgov = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'parlgov'").fetchall()
        }
        elections: dict[str, list[tuple[date, UUID]]] = defaultdict(list)
        for iso, day, election in conn.execute(
            """select distinct p.iso_code, e.date_from, coalesce(e.parent_id, e.id)
               from election e join polity p on p.id = e.polity_id
               join contest c on c.election_id = e.id
               join body b on b.id = c.body_id and b.polity_id = p.id and b.kind in ('lower_house', 'unicameral')
               where e.kind in ('general', 'runoff') and p.iso_code = any(%s)""",
            [list(COUNTRIES.values())],
        ).fetchall():
            elections[iso].append((day, election))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Cabinets of Germany, Turkey, Canada, Italy, the United Kingdom, and France from ParlGov",
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
            attribution="Döring, Holger, Constantin Huber, and Philip Manow. 2024. ParlGov 2024 Release. Harvard Dataverse.",
        )
        cs.artifact(raw.cached(SOURCE, DATABASE), source)
        wikidata = cs.source("Wikidata", HEADS)
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title=HEADS,
            url="https://www.wikidata.org",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, _holders_url()), wikidata)
        for url in [url for url, _ in _lookups(db)] + _labels(db):
            cs.artifact(raw.cached(SOURCE, url), wikidata)
        created: set[UUID] = set()

        def entity(item: str, kind: str, iso: str, name: str) -> UUID:
            found = cs.key("wikidata", item)
            if found in cs.minted and found not in created:
                created.add(found)
                if kind == "party":
                    cs.add("party", id=found, kind="party", polity_id=cs.polity(iso))
                else:
                    cs.add("person", id=found)
                cs.add(
                    "name",
                    entity_id=found,
                    text=labels.get(item) or name,
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
            return found

        offices: dict[tuple[str, str], UUID] = {}
        for (iso, key), name in OFFICES.items():
            office = cs.key(f"{iso.lower()}.office", key)
            offices[(iso, key)] = office
            if office in cs.minted:
                cs.add("office", id=office, polity_id=cs.polity(iso), kind="head_of_government")
                cs.add("name", entity_id=office, text=name, lang="en", kind="english", is_preferred=True)
        offices[("TR", "president")] = cs.key("tr.office", "president")

        def election(iso: str, day: date | None) -> UUID | None:
            if day is None:
                return None
            return next((found for held, found in elections[iso] if abs((held - day).days) <= 14), None)

        governments: list[
            tuple[str, str, date, bool, date | None, list[tuple[UUID, str, int | None]], tuple[str, str]]
        ] = []
        missing: list[str] = []
        for cabinet in cabinets:
            iso = COUNTRIES[cabinet["country"]]
            start = date.fromisoformat(cabinet["start_date"])
            rows: dict[UUID, tuple[UUID, str, int | None]] = {}
            for member in members[cabinet["id"]]:
                splits = SPLITS.get(member["party_id"])
                if splits is not None:
                    chosen = [
                        item
                        for item, since, until in splits
                        if (since is None or since <= start) and (until is None or start < until)
                    ]
                    for index, item in enumerate(chosen):
                        party = entity(item, "party", iso, member["name_english"])
                        role = "head" if member["pm"] and index == 0 else "member"
                        rows[party] = (party, role, member["seats"] if len(chosen) == 1 else None)
                    continue
                item = parties.get(member["party_id"])
                if member["party_id"] in UNLINKED:
                    party = cs.key("parlgov", str(member["party_id"]))
                    if party in cs.minted and party not in created:
                        created.add(party)
                        cs.add("party", id=party, kind="party", polity_id=cs.polity(iso))
                        cs.add(
                            "name",
                            entity_id=party,
                            text=member["name_english"],
                            lang="en",
                            kind="english",
                            is_preferred=True,
                        )
                elif item is None:
                    continue
                else:
                    party = entity(item, "party", iso, member["name_english"] or member["name"])
                    if str(member["party_id"]) not in parlgov:
                        parlgov.add(str(member["party_id"]))
                        cs.add("external_id", entity_id=party, scheme="parlgov", value=str(member["party_id"]))
                rows[party] = (party, "head" if member["pm"] else "member", member["seats"])
            governments.append(
                (
                    iso,
                    cabinet["name"].strip(),
                    start,
                    bool(cabinet["caretaker"]),
                    None if cabinet["election_date"] is None else date.fromisoformat(cabinet["election_date"]),
                    list(rows.values()),
                    ("parlgov.cabinet", str(cabinet["id"])),
                )
            )
        for iso, name, start, followed, leader, partners in RECENT:
            if head(iso, name, start) is None:
                missing.append(f"{iso} {start} {name} (left out)")
                continue
            lineup = [(entity(leader, "party", iso, leader), "head", None)] + [
                (entity(partner, "party", iso, partner), "member", None) for partner in partners
            ]
            governments.append((iso, name, start, False, followed, lineup, ("ea.government", f"{iso}:{start}")))
        governments.sort(key=lambda government: (government[0], government[2]))
        previous: dict[str, UUID | None] = {}
        for index, (iso, name, start, caretaker, followed, lineup, key) in enumerate(governments):
            following = governments[index + 1] if index + 1 < len(governments) else None
            end = ENDS.get(name) or (following[2] if following is not None and following[0] == iso else None)
            government = cs.key(*key)
            leader = head(iso, name, start)
            if leader is None:
                missing.append(f"{iso} {start} {name}")
            person = None if leader is None else entity(leader[0], "person", iso, leader[2])
            cs.add(
                "government",
                id=government,
                polity_id=cs.polity(iso),
                office_id=None if leader is None else offices[(iso, leader[1])],
                head_person_id=person,
                from_date=start,
                from_precision="day",
                to_date=end,
                to_precision="day",
                election_id=election(iso, followed),
                previous_government_id=previous.get(iso),
                caretaker=caretaker,
                source_id=source if key[0] == "parlgov.cabinet" else wikidata,
            )
            cs.add("name", entity_id=government, text=name, lang="en", kind="short", is_preferred=True)
            for party, role, seats in lineup:
                cs.add("government_party", government_id=government, party_id=party, role=role, seats=seats)
            previous[iso] = None if name in ENDS else government
        if missing:
            print("no head of government for", "; ".join(missing))
        return cs.write()
