import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import enr_oh
from ea.sources.us.enr_2026 import get, terms
from ea.store import DATA, connect

SOURCE = "us.oh_general_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
FEED = enr_oh.FEED
PAGE = "https://liveresults.ohiosos.gov/"
PUBLISHER = "Ohio Secretary of State"
TITLE = "Election night reporting: candidates on the November 3, 2026 general election ballot"
PARTIES = {"DEM": "democratic-party", "REP": "republican-party", "LIB": "libertarian-party"}
INDEPENDENT = {"Indep", "Nonprtycan"}
NOTE = "Besides the nominees recorded in the archive's 2026 primary result sets, the candidates are those on the Ohio Secretary of State's election night reporting ballot for the 2026-11-03 general election, which includes independent and Libertarian candidates. Declared write-in candidates are not listed."
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch() -> None:
    try:
        raw.cached(SOURCE, FEED)
    except LookupError:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=120) as client:
            content = get(client, FEED)
        if content is None or b"<ElectionReport" not in content[:2000]:
            raise RuntimeError("Ohio results feed unavailable")
        raw.store(
            SOURCE,
            FEED,
            content,
            suffix=".xml",
            retrieved_at=datetime.now(UTC),
            media_type="application/xml",
        )


def ballot() -> dict[str, list[tuple[str, str]]]:
    root = ElementTree.parse(raw.cached(SOURCE, FEED).path).getroot()
    ns = enr_oh.NS
    parties = {party.get("ObjectId"): party.get("Abbreviation") or "" for party in root.iter(f"{ns}Party")}
    people = {
        candidate.get("ObjectId"): (enr_oh.text(candidate.find(f"{ns}BallotName")), candidate.findtext(f"{ns}PartyId"))
        for candidate in root.iter(f"{ns}Candidate")
    }
    races: dict[str, list[tuple[str, str]]] = {}
    for contest in root.iter(f"{ns}Contest"):
        seat = enr_oh.SEAT.fullmatch(" ".join((contest.get("Name") or "").split()))
        if seat is None:
            continue
        district = str(int(seat[2]))
        key = f"{DAY}:OH:" + (
            f"house:{district}"
            if seat[1] == "Representative to Congress"
            else f"{'senate' if seat[1] == 'State Senator' else 'house'}:{district}:g"
        )
        for selection in contest.findall(f"{ns}BallotSelection"):
            name, party_id = people.get(selection.findtext(f"{ns}CandidateId"), ("", None))
            party = parties.get(party_id, "")
            if name and party != "Write-in":
                races.setdefault(key, []).append((name, party))
    return races


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    races = ballot()
    lines = [f"{key}\t{name}\t{party}" for key, rows in sorted(races.items()) for name, party in sorted(rows)]
    (PARSED / "candidates.tsv").write_text("\n".join(lines) + "\n")
    print(f"{sum(len(rows) for rows in races.values())} candidates in {len(races)} contests")


def words(label: str) -> list[str]:
    return terms(re.sub(r"\(write-in\)", " ", label, flags=re.IGNORECASE))


def same(name: str, label: str) -> bool:
    wanted, held = (
        terms(name, joined=False),
        terms(re.sub(r"\(write-in\)", " ", label, flags=re.IGNORECASE), joined=False),
    )
    return bool(wanted and held) and wanted[-1] == held[-1] and wanted[0][:3] == held[0][:3]


def matches(name: str, label: str) -> bool:
    tokens = {*terms(name), *terms(name, joined=False)}
    held = terms(label)
    return bool(held) and (held[-1] in tokens or "".join(held[-2:]) in tokens)


def emit() -> Path:
    races: dict[str, list[tuple[str, str]]] = {}
    for line in (PARSED / "candidates.tsv").read_text().splitlines():
        key, name, party = line.split("\t")
        races.setdefault(key, []).append((name, party))
    with connect() as conn:
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value like %s",
                [f"{DAY}:OH:%"],
            ).fetchall()
        )
        held: dict[str, list[tuple[UUID, str, UUID | None, UUID | None, UUID | None]]] = {}
        for value, option, label, party, candidacy, person in conn.execute(
            """select x.value, op.id, op.ballot_label, op.party_id, cy.id, cy.person_id
               from external_id x
               join contest_option op on op.contest_id = x.entity_id and op.kind = 'candidate'
               left join candidacy cy on cy.option_id = op.id and cy.role = 'head'
               where x.scheme = 'us.contest' and x.value like %s
                 and not exists (select from entity_retirement r where r.entity_id = op.id)""",
            [f"{DAY}:OH:%"],
        ).fetchall():
            held.setdefault(value, []).append((option, label, party, candidacy, person))
        earlier: dict[str, set[UUID]] = {}
        for label, person in conn.execute(
            """select op.ballot_label, cy.person_id
               from external_id x
               join contest_option op on op.contest_id = x.entity_id and op.kind = 'candidate'
               join candidacy cy on cy.option_id = op.id and cy.role = 'head'
               where x.scheme = 'us.contest' and x.value ~ '^[0-9-]+:OH:' and x.value not like %s""",
            [f"{DAY}:OH:%"],
        ).fetchall():
            if label:
                earlier.setdefault(slug(" ".join(words(label)[-1:])), set()).add(person)
        noted = {
            entity
            for (entity,) in conn.execute(
                """select n.entity_id from note n join changeset c on c.id = n.changeset_id and c.pipeline = 'us.fec_2026'
                   where n.kind = 'methodology' and n.body_md like 'Candidates are the Ohio nominees%%'"""
            ).fetchall()
        }
        names = dict(
            conn.execute(
                "select entity_id, text from name where is_preferred and lang = 'en' and entity_id = any(%s)",
                [list({person for people in earlier.values() for person in people})],
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's November 3, 2026 general election candidates missing from the archive, from the Secretary of State's election night reporting ballot",
            polity="US-OH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=PAGE)
        cs.artifact(raw.cached(SOURCE, FEED), source)
        counts = {"added": 0, "relabeled": 0, "retired": 0}
        for key, rows in sorted(races.items()):
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest")
                continue
            current = held.get(key, [])
            used: set[UUID] = set()
            for name, party in sorted(rows):
                found = next(
                    (entry for entry in current if entry[0] not in used and entry[1] and same(name, entry[1])), None
                )
                if found is not None:
                    used.add(found[0])
                    if not matches(name, found[1]):
                        cs.add(
                            "contest_option",
                            id=found[0],
                            contest_id=contest,
                            kind="candidate",
                            party_id=found[2],
                            ballot_label=name,
                        )
                        counts["relabeled"] += 1
                    continue
                pool = {
                    person
                    for person in earlier.get(slug(" ".join(words(name)[-1:])), set())
                    if names.get(person) and same(name, names[person])
                }
                if len(pool) == 1:
                    person = next(iter(pool))
                else:
                    person = cs.key("us.fec_2026.person", f"OH:{slug(name)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                party_id = cs.key("us.party", PARTIES[party]) if party in PARTIES else None
                if party_id is None and party not in INDEPENDENT:
                    print(f"{key}: {name} has party {party!r}")
                    continue
                fresh = f"{key}:{slug(name)}"
                option = cs.key("us.option", fresh)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=name,
                )
                candidacy = cs.key("us.candidacy", fresh)
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    is_independent=party_id is None,
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                counts["added"] += 1
            for option, label, _, candidacy, _ in current:
                if option not in used and label and "(write-in)" in label:
                    reason = "Not on the Ohio Secretary of State's ballot for the 2026 general election"
                    if candidacy is not None:
                        cs.add("entity_retirement", entity_id=candidacy, reason=reason)
                    cs.add("entity_retirement", entity_id=option, reason=reason)
                    counts["retired"] += 1
            if contest in noted:
                cs.add("note", entity_id=contest, kind="methodology", body_md=NOTE)
        print(counts)
        return cs.write()
