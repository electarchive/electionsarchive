import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import fec
from ea.store import connect

SOURCE = "us.match"
VERSION = "3"
TITLE = "Candidate Master Files, 1982–2000"
NOMINEES = {
    "P20000642": "william-j-clinton",
    "P80000912": "albert-gore-jr",
    "H6TN04036": "albert-gore-jr",
    "S4TN00039": "albert-gore-jr",
    "S2MN00019": "walter-f-mondale",
    "H6NY38029": "jack-kemp",
    "S0IN00012": "james-danforth-quayle",
    "S6TX00016": "lloyd-bentsen",
    "H8NY09034": "geraldine-a-ferraro",
    "S2NY00119": "geraldine-a-ferraro",
    "S4KS00010": "robert-dole",
    "H8WY00015": "richard-b-cheney",
}
MERGES = {
    ("us.fec.person", "michael-ss-dukakis"): ("us.person", "michael-s-dukakis"),
    ("us.fec.person", "tr-larouche-lyndon-h"): ("us.fec.person", "VA:lyndon-h-larouche-jr"),
    ("us.fec.person", "isabelle-masters"): ("us.fec.person", "isabell-masters"),
    **{
        ("fec.candidate", fec_id): ("fec.candidate", "P60016342")
        for fec_id in [
            "S6FL00509",
            "S8CA00598",
            "S8DE00178",
            "S8HI00228",
            "S8MN00719",
            "S8RI00151",
            "S8VT00166",
            "S8WA00384",
            "S8WY00205",
        ]
    },
    ("fec.candidate", "H0CA21104"): ("fec.candidate", "H8FL24067"),
    ("fec.candidate", "H0TX27087"): ("fec.candidate", "H8FL24067"),
    ("fec.candidate", "H0CA13051"): ("fec.candidate", "S4CA00332"),
}


def emit() -> Path:
    rows = pl.concat(
        [pl.read_parquet(fec.PARSED / "books.parquet"), pl.read_parquet(fec.PARSED / "books_primaries.parquet")],
        how="diagonal_relaxed",
    ).filter((pl.col("chamber") != "president") & ~pl.col("name").is_in(["Scattered", fec.NOTA, fec.VACANCY]))
    earlier = {(year, state, fixed): mangled for (year, state, mangled), fixed in fec.RENAMED.items()}
    with connect() as conn:
        keyed: dict[tuple[str, str], UUID] = {
            (scheme, value): entity
            for scheme, value, entity in conn.execute(
                """select scheme, value, entity_id from external_id
                   where scheme in ('fec.candidate', 'us.fec.person', 'us.person')"""
            ).fetchall()
        }
        spoken: dict[UUID, set[str]] = {}
        surnames: dict[UUID, set[str]] = {}
        for holder, text in conn.execute(
            """select x.entity_id, n.text from external_id x join name n on n.entity_id = x.entity_id
               where x.scheme = 'fec.candidate'"""
        ).fetchall():
            words = [word for word in slug(text).split("-") if word not in fec.SUFFIXES]
            spoken.setdefault(holder, set()).update(word for word in words if len(word) > 2)
            surnames.setdefault(holder, set()).update(words[-1:])
        candidacies: dict[UUID, int] = dict(
            conn.execute("select person_id, count(*) from candidacy group by person_id").fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="People matched by FEC candidate ID across the FEC's books, its filings, and the Electoral College, and each Electoral College vote placed under the general election that chose its electors",
            polity="US",
            kind="match",
        )
        source = cs.source(fec.PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=fec.PUBLISHER,
            title=TITLE,
            url="https://www.fec.gov/data/browse-data/?tab=bulk-data",
            license="Public domain",
        )
        for url in fec.MASTERS.values():
            cs.artifact(raw.cached(fec.SOURCE, url), source)

        parent: dict[str, str] = {}

        def root(node: str) -> str:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def join(one: str, other: str) -> None:
            parent[root(one)] = root(other)

        mismatched: list[tuple[int, str, str, str]] = []
        for year, state, name, fec_id in rows.select("year", "state", "name", "fec_id").unique().iter_rows():
            person = keyed.get(("us.fec.person", f"{state}:{slug(name)}"))
            if person is None and (year, state, name) in earlier:
                person = keyed.get(("us.fec.person", f"{state}:{slug(earlier[(year, state, name)])}"))
            if person is None or fec_id is None:
                continue
            holder = keyed.get(("fec.candidate", fec_id))
            if holder is not None and holder != person and not spoken.get(holder, set()) & set(slug(name).split("-")):
                mismatched.append((year, state, name, fec_id))
            join(f"p:{person}", f"f:{fec_id}")
        moved = {fec_id for _, _, _, fec_id in mismatched}
        for (scheme, value), entity in keyed.items():
            if scheme == "fec.candidate" and value not in moved:
                join(f"f:{value}", f"p:{entity}")
        for merged, target in MERGES.items():
            if merged in keyed and target in keyed:
                join(f"p:{keyed[merged]}", f"p:{keyed[target]}")
        for fec_id, handle in [
            *NOMINEES.items(),
            *((key, handle) for key, handle in fec.LINKS.items() if re.fullmatch(r"[HSP]\w{8}", key)),
        ]:
            if ("us.person", handle) in keyed:
                join(f"f:{fec_id}", f"p:{keyed[('us.person', handle)]}")

        members: dict[str, list[str]] = {}
        for node in list(parent):
            members.setdefault(root(node), []).append(node)
        nominees = {entity for (scheme, _), entity in keyed.items() if scheme == "us.person"}
        holders = {entity for (scheme, _), entity in keyed.items() if scheme == "fec.candidate"}
        targets = {keyed[target] for target in MERGES.values() if target in keyed}
        merges, attached, conflicts = 0, 0, []
        for nodes in members.values():
            people = sorted({UUID(node[2:]) for node in nodes if node.startswith("p:")}, key=str)
            leading = (
                [person for person in people if person in nominees]
                or [person for person in people if person in holders]
                or [person for person in people if person in targets]
            )
            if not people:
                continue
            if len(leading) > 1 and (
                any(person in nominees for person in leading)
                or not set.intersection(*(surnames.get(person, set()) for person in leading))
            ):
                conflicts.append(sorted(nodes))
                continue
            survivor = max(leading or people, key=lambda person: candidacies.get(person, 0))
            for person in people:
                if person != survivor:
                    cs.add("entity_merge", entity_id=person, into_id=survivor)
                    merges += 1
            for node in sorted(nodes):
                if node.startswith("f:") and (("fec.candidate", node[2:]) not in keyed or node[2:] in moved):
                    cs.add("external_id", entity_id=survivor, scheme="fec.candidate", value=node[2:])
                    attached += 1
        stages = conn.execute(
            """select k.id, k.polity_id, k.kind::text, k.date_from, k.date_to, k.status::text,
                 coalesce(
                   (select p.election_id from contest c join contest p on p.id = c.previous_round_id
                    where c.election_id = k.id and p.election_id <> k.id limit 1),
                   (select g.id from election g
                    where g.polity_id = k.polity_id and g.kind = 'general' and g.parent_id is null
                      and g.date_from between k.date_from - 75 and k.date_from - 1
                    order by g.date_from desc limit 1))
               from election k join external_id x on x.entity_id = k.id and x.scheme = 'us.nara.election'
               where k.kind = 'indirect'"""
        ).fetchall()
        linked = 0
        for election, polity, kind, start, end, status, umbrella in stages:
            if umbrella is not None:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=umbrella,
                    kind=kind,
                    date_from=start,
                    date_to=end,
                    status=status,
                )
                linked += 1
        print(f"{linked} of {len(stages)} Electoral College votes and contingent elections placed under their cycle")
        print(f"{merges} merges, {attached} FEC IDs attached, {len(conflicts)} groups with several FEC people skipped")
        for conflict in conflicts[:40]:
            print(conflict)
        print(f"{len(mismatched)} FEC IDs moved from a differently named person to the book's:")
        for line in mismatched:
            print(line)
        return cs.write()
