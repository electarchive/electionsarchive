import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "identity"
VERSION = "1"
NEAR = 12 * 366
LONG_GAP = 24 * 365
LONG_SPAN = 50 * 365
MATCHED = ("tr.",)
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
IDENTITIES = (
    "bioguide",
    "wikidata",
    "mnis",
    "ep.mep",
    "lop.person",
    "fec.candidate",
    "us.klarner.person",
    "us.alged.person",
    "us.elstats_local.person",
)
STRONG = ("bioguide", "wikidata", "mnis", "ep.mep", "lop.person", "fec.candidate")
PARTIES = {
    "Colorado's Commonwealth Party, 1930": (
        "6a73b7d9-da80-5785-ac0b-c40a34ad2fc2",
        "d44b6311-7ffb-584b-9ad4-d6914fc29d74",
    ),
    "Colorado's Communist Party, 1924–1942": (
        "0654089f-dae7-503f-987d-71e42d5f7415",
        "5f9b364f-1fce-59ed-b556-e896f2a551a0",
    ),
    "Colorado's Concerns of People Party, 1984–2004": (
        "01a13d06-0e48-761d-8c22-86f2e7e7ea1f",
        "01a13afb-20b0-740c-bd6d-a52e6a8abb52",
    ),
    "Colorado's Farmer-Labor Party, 1920–1932": (
        "784658bc-a316-56a8-bc3c-aa8b949293c3",
        "4be8b07b-af60-57da-afea-1edfc1a00d4e",
    ),
    "Colorado's Independent American Party, 1970": (
        "dcbf5473-de91-50f9-b686-94aab7471796",
        "01a13afb-20b0-740c-bd6d-a53165e39f64",
    ),
    "Colorado's Liberal Party, 1916–1930": (
        "bbc9b07e-bcf8-58a1-8e03-b397ea5a0446",
        "8a77f70c-5c7e-5b47-8e15-df4e2d65bc65",
    ),
    "Colorado's National Statesman Party, 1978": (
        "fa5309a5-be7e-5600-b29f-d5a835a3a010",
        "1453d00e-b831-572e-9b24-b64c604ccd03",
    ),
    "Colorado's National Union Party, 1936": (
        "9b31ab84-1466-574e-a751-810a6a4e9665",
        "ac2dd28b-71f0-51aa-8b20-481bebf58282",
    ),
    "Colorado's New Hispano Party, 1966": (
        "6d16d91c-4e9e-5bc2-aff8-6d4b8a20a8ef",
        "2eb17c29-f482-5770-89e4-328bf42c0ab3",
    ),
    "Colorado's La Raza Unida Party, 1970–1976": (
        "fe633eda-81eb-5f21-8db1-a2ae79e829b2",
        "9b96b5d1-621b-5b48-88ef-2ad5e7267d11",
        "33db7e99-c2d5-594b-a4c8-c1babcc3f90e",
    ),
    "Colorado's Socialist Party, 1910–1954": (
        "a3b2a511-c401-52f1-ab00-d4836a2ece55",
        "d1bc74f5-b397-5683-983b-aa71e5a968c2",
    ),
    "Colorado's Socialist Labor Party, 1910–1962": (
        "d3330fdc-94ee-5ba2-bc03-97303e52b18a",
        "8f73747d-d2fe-597e-948c-eaf984f42f6a",
    ),
    "Colorado's Statesman Party, 1980": (
        "4ea39368-6f1c-5e73-9457-d1fc6bd78d05",
        "50f3b208-c843-527f-8d94-9b4fea0ba075",
    ),
    "Connecticut's George Wallace Party, 1974–1976": (
        "f995b65e-9109-5892-b841-af4954026d38",
        "97b81568-8286-5d0f-8395-1d1246cc9468",
    ),
    "Connecticut's Socialist Party, 1902–1958": (
        "37a7f419-d544-5639-8f07-5d8ecb470398",
        "bae82bde-46dd-5a90-9809-45871a794fd1",
    ),
    "Delaware's American Party, 1970–1986": (
        "01a13d06-0e49-7148-b8b7-0f226c6672d6",
        "9ec92b59-f1ca-5d2e-b8ac-58ce9265d92d",
    ),
    "New York's Peace and Freedom Party, 1966–2024": (
        "1d1a4253-9460-5954-9c80-73e324ba531b",
        "9e0ae2c2-bb58-5025-b3dc-0443e0cdcdd6",
    ),
    "New York's Socialist Labor Party, 1920–1980": (
        "43392e40-d80a-541a-8d4b-9527dfa612cc",
        "e30d5afc-f2ae-5473-b5b8-622c7f2c09e0",
    ),
    "Rhode Island's Peace and Freedom Party, 1970": (
        "252002e9-9af6-5664-a163-7555d61fb8ff",
        "3ed7f2a6-3330-5bae-8408-8affda0f7fc1",
    ),
    "Washington's Peace and Freedom Party, 1966–1968": (
        "830e6219-06ac-5294-aee1-12dbb33083c2",
        "9add6fbe-426e-58df-a5e0-a02d81ed4be7",
    ),
    "Washington's Workers Party of America, 1922–1928": (
        "34bedb9b-7648-5c45-bcc2-b715e74896da",
        "89699ab9-3b06-59cb-8c76-34c897df4079",
    ),
    "Washington's Human Rights Party, 2014–2018": (
        "01a13c42-ee25-76a4-818e-73233326c6fe",
        "01a13c42-ee25-76a4-818e-7335278d98a4",
    ),
    "Wisconsin's Conservative Party, 1968–2000": (
        "c068e259-69d5-54dc-ac8c-a30cd1d84178",
        "e13710af-eecc-5bd4-8f33-fce2aa3ebdee",
    ),
    "Texas's La Raza Unida Party, 1972–1978": (
        "89071ad5-2531-5a81-8dd9-e2d3b75b2bcc",
        "c46e7602-9cd3-5310-8e69-baf380b96868",
        "c7ca1346-d7c8-5142-a33f-00057ca0d204",
    ),
    "New Mexico's La Raza Unida Party, 1976–1982": (
        "6add521b-d73e-59fb-84ea-549247482abd",
        "fc5eb5d4-40d3-5d48-ac40-f08bbb49c75a",
    ),
    "California's Peace and Freedom Party, 1968–2022": (
        "01a13afb-20b0-740c-bd6d-a525349071c9",
        "01a13d06-0e48-761d-8c22-86ebd8eb40c3",
    ),
    "Puerto Rico's New Progressive Party, 1988–2024": (
        "01a13afb-20b1-714a-a374-9742fb668fc3",
        "01a13d06-0e4a-70ac-9628-9d2e0c5a977e",
    ),
    "Puerto Rico's Popular Democratic Party, 1992–2024": (
        "01a13afb-20b1-714a-a374-97448cea3784",
        "01a13d06-0e4a-70ac-9628-9d2fe1550ed1",
    ),
    "Puerto Rico's Independence Party, 1992–2024": (
        "01a13afb-20b1-714a-a374-97436bc4715d",
        "01a13d06-0e4a-70ac-9628-9d31fb7c8a7c",
    ),
    "Virginia's Socialist Party, 1900–1949": (
        "1cf3d06b-c63f-5e1f-8541-d19e1d1568b2",
        "dd1f834d-5292-580e-bcf4-316629e08948",
    ),
    "Minnesota's National Party, 1918": (
        "9c3de61d-c4df-5d37-a21c-1ba3f722474d",
        "c81fd68a-174f-5617-a955-c317d0fd67c7",
    ),
    "Montana's National Party, 1918": ("18f5ba62-f4ea-590f-8dca-a5d6976fa972", "bf15d6a7-643d-5720-99d8-921981a03f71"),
    "Church of the Militant Elvis Party, 2001–2019": (
        "b517c147-266b-5eba-b997-4b6f6de81f19",
        "412743ed-b36a-5909-a462-0b5cdd163dc3",
    ),
    "English National Party, 1974–1979": (
        "1c06bc7e-5b67-51ec-95f3-f4110b32664e",
        "cf5953ef-3fd9-5158-8e74-b5631824f0a9",
    ),
    "Raving Loony Green Giant Party, 1992": (
        "3dcb5389-21c2-5c47-9eaf-7fb8069fd235",
        "f3e4323f-ba5b-5cb5-9bf9-211017bd8cbb",
    ),
    "Socialist Equality Party, 1997–2024": (
        "0173f891-401a-53f5-83c8-16c9680116cc",
        "b7202e7d-975f-522e-9fb6-0b702e67702f",
    ),
    "United Country Party, 1979": ("61f32858-6c1d-505a-a44b-6a3892cd0691", "693e8635-419a-5630-8278-eeccb8410cd3"),
    "United Democratic Party, October 1974": (
        "660f1bd3-ffae-58c7-90eb-de1f2995d6f1",
        "7f9bc066-497d-50c2-9431-c753fd45a8d1",
    ),
    "Coalition Labour, 1918, in ParlGov and Umit": (
        "916e1c91-21d2-5d81-a2fa-75b0001ffce7",
        "49d5173d-bed6-5032-a935-5fa6da7b6a4f",
    ),
    "Canada's Liberal-Labour candidates, 1926–1968": (
        "01a13fb9-5ed7-71fc-acca-7e45df10bcc6",
        "01a13fb9-5ed7-71fc-acca-7e4ad5d5d1ec",
    ),
    "Republican Party of Canada, 1964–1971": (
        "01a13fb9-5ed8-727d-a580-0593f27ddb58",
        "01a13fb9-5ed8-727d-a580-059acefcd61e",
    ),
    "Gesamtdeutsche Partei, 1961 and 1969 (printed GPD in 1969)": (
        "82a4ab1b-17e3-50a9-8b0c-99cc31525c08",
        "18c52535-0f3f-54ed-93f8-0b79a0a03ad4",
    ),
    "Unione Sudamericana Emigrati Italiani, 2006–2018": (
        "6bf66be8-b947-5ab5-9455-21ef9c57ec23",
        "a973ef8e-c9b3-54df-83cf-80c20cfc21cb",
    ),
}
PEOPLE = {
    "Mark Dayton, senator and governor of Minnesota": (
        ("bioguide", "D000596"),
        ("fec.candidate", "S2MN00027"),
        ("us.algara.person", "MN:mark-dayton"),
    ),
}
PLACEHOLDERS = re.compile(
    r"\b(unknown|scatter\w*|write[- ]?ins?|all other|others?|blank|void|none of|uncommitted|vacan\w*|no candidate)\b"
)


def words(text: str) -> tuple[str, frozenset[str]] | None:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text.lower().replace("ı", "i")) if not unicodedata.combining(c)
    )
    folded = re.sub(r"\([^)]*\)|\"[^\"]*\"|“[^”]*”", " ", folded)
    if PLACEHOLDERS.search(folded):
        return None
    parts = [part for part in re.split(r"[^a-z]+", folded) if part]
    if not parts:
        return None
    kept = [parts[0], *(part for part in parts[1:] if len(part) > 1 or part in SUFFIXES)]
    if len(kept) < 2:
        return None
    return " ".join(kept), frozenset(part for part in parts[1:] if len(part) == 1)


def emit() -> Path:
    with connect() as conn:
        conn.execute("set statement_timeout = 0")
        busy: set[str] = {
            pipeline
            for (pipeline,) in conn.execute(
                "select distinct pipeline from changeset where status in ('draft', 'pending_review')"
            ).fetchall()
        }
        rows: list[tuple[UUID, str | None, UUID, UUID | None, UUID | None, date, str]] = conn.execute(
            """select cy.person_id, coalesce(cy.ballot_name, n.text), k.district_area_id, k.office_id, k.body_id,
                      e.date_from, c.pipeline
               from candidacy cy
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               join changeset c on c.id = cy.changeset_id
               left join lateral (select text from name where entity_id = cy.person_id and is_preferred limit 1) n
                 on true
               where not exists (select from entity_merge m where m.entity_id = cy.person_id)"""
        ).fetchall()
        held: dict[UUID, dict[str, set[str]]] = defaultdict(dict)
        for person, scheme, value in conn.execute(
            "select entity_id, scheme, value from external_id where scheme = any(%s)", [list(IDENTITIES)]
        ).fetchall():
            held[person].setdefault(scheme, set()).add(value)
        created: dict[UUID, datetime] = dict(
            conn.execute("select id, created_at from entity where type = 'person'").fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "People recorded once per source or per race joined into one person where the same printed name "
                "(ignoring case, accents, and middle initials) ran for the same office or body in the same "
                "district within 12 years, and nothing tells them apart, and party labels of one state or country "
                "that sources spelled apart joined by hand"
            ),
            polity=None,
            kind="match",
        )

        dates: dict[UUID, list[date]] = defaultdict(list)
        pipelines: dict[UUID, set[str]] = defaultdict(set)
        lines: dict[tuple[object, ...], dict[UUID, list[date]]] = defaultdict(lambda: defaultdict(list))
        initials: dict[tuple[tuple[object, ...], UUID], set[frozenset[str]]] = defaultdict(set)
        for person, text, area, office, body, day, pipeline in rows:
            dates[person].append(day)
            pipelines[person].add(pipeline)
            if text is None or area is None or (office is None and body is None) or pipeline.startswith(MATCHED):
                continue
            named = words(text)
            if named is None:
                continue
            line = (area, office, body, named[0])
            lines[line][person].append(day)
            if named[1]:
                initials[line, person].add(named[1])

        def suspect(person: UUID) -> bool:
            days = sorted(dates[person])
            gaps = [(later - earlier).days for earlier, later in pairwise(days)]
            return (
                bool(pipelines[person] & busy)
                or (days[-1] - days[0]).days > LONG_SPAN
                or max(gaps, default=0) > LONG_GAP
            )

        parent: dict[UUID, UUID] = {}

        def root(node: UUID) -> UUID:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        considered: set[UUID] = set()
        blocked: list[tuple[UUID, UUID]] = []
        for line, people in lines.items():
            if len(people) < 2:
                continue
            members = [person for person in people if not suspect(person)]
            for index, person in enumerate(members):
                for other in members[index + 1 :]:
                    signed = initials[line, person] | initials[line, other]
                    if set(dates[person]) & set(dates[other]) or len(signed) > 1:
                        blocked.append((person, other))
                        continue
                    if min(abs((a - b).days) for a in people[person] for b in people[other]) <= NEAR:
                        considered.update((person, other))
                        parent[root(person)] = root(other)

        groups: dict[UUID, list[UUID]] = defaultdict(list)
        for person in considered:
            groups[root(person)].append(person)
        refused = {root(a) for a, b in blocked if a in parent and b in parent and root(a) == root(b)}
        merged = skipped = 0
        for key, people in groups.items():
            schemes = defaultdict(set)
            for person in people:
                for scheme, values in held[person].items():
                    schemes[scheme] |= values
            if key in refused or any(len(values) > 1 for values in schemes.values()):
                skipped += 1
                continue
            survivor = max(
                people,
                key=lambda person: (
                    any(scheme in held[person] for scheme in STRONG),
                    len(dates[person]),
                    -created[person].timestamp(),
                    -person.int,
                ),
            )
            for person in people:
                if person != survivor:
                    cs.merge(person, survivor)
                    merged += 1
        print(f"{len(groups)} groups, {merged} merged, {skipped} skipped, deferred pipelines: {sorted(busy)}")

        parties: dict[UUID, tuple[str, int, bool, set[str]]] = {
            party: (polity, options, linked, set(writers))
            for party, polity, options, linked, writers in conn.execute(
                """select p.id, p.polity_id::text, (select count(*) from contest_option o where o.party_id = p.id),
                          exists (select from external_id x where x.entity_id = p.id and x.scheme = 'wikidata'),
                          array_remove(array[c.pipeline] || array(select distinct w.pipeline from contest_option o
                                       join changeset w on w.id = o.changeset_id where o.party_id = p.id), null)
                   from party p join changeset c on c.id = p.changeset_id
                   where p.id = any(%s) and not exists (select from entity_merge m where m.entity_id = p.id)""",
                [[UUID(party) for group in PARTIES.values() for party in group]],
            ).fetchall()
        }
        for label, group in PARTIES.items():
            members = [UUID(party) for party in group]
            if not all(party in parties for party in members):
                print(f"{label}: already merged or missing")
                continue
            if len({parties[party][0] for party in members}) > 1:
                raise ValueError(f"{label}: parties of different polities")
            if any(parties[party][3] & busy for party in members):
                print(f"{label}: deferred")
                continue
            survivor = max(members, key=lambda party: (parties[party][2], parties[party][1]))
            for party in members:
                if party != survivor:
                    cs.merge(party, survivor)
        nuances: dict[str, list[tuple[UUID, str | None, int]]] = defaultdict(list)
        for party, text, item, options in conn.execute(
            """select p.id, n.text,
                      (select x.value from external_id x where x.entity_id = p.id and x.scheme = 'wikidata' limit 1),
                      (select count(*) from contest_option o where o.party_id = p.id)
               from party p join changeset c on c.id = p.changeset_id
               join name n on n.entity_id = p.id and n.is_preferred
               where c.pipeline in ('fr.cdsp', 'fr.interieur')
                 and not exists (select from entity_merge m where m.entity_id = p.id)"""
        ).fetchall():
            folded = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
            nuances[folded.lower().rstrip("s")].append((party, item, options))
        for members in nuances.values():
            if len(members) < 2 or len({item for _, item, _ in members if item}) > 1:
                continue
            survivor = max(members, key=lambda member: (member[1] is not None, member[2]))[0]
            for party, _, _ in members:
                if party != survivor:
                    cs.merge(party, survivor)
        for label, keys in PEOPLE.items():
            found = [
                person
                for scheme, value in keys
                for (person,) in conn.execute(
                    """select x.entity_id from external_id x where x.scheme = %s and x.value = %s
                       and not exists (select from entity_merge m where m.entity_id = x.entity_id)""",
                    [scheme, value],
                ).fetchall()
            ]
            if len(found) != len(keys):
                print(f"{label}: already merged or missing")
            for person in dict.fromkeys(found[1:]):
                if person != found[0]:
                    cs.merge(person, found[0])
        return cs.write()
