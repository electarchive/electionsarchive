import csv
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.party_members"
VERSION = "1"
ROWS = Path(__file__).with_name("party_members.tsv")
MERGES = [(("tr.ysk_local.person", "2014-03-30:istanbul-beylikduzu-mayor"), ("wikidata", "Q59313241"))]


def _rows() -> list[dict[str, str]]:
    with ROWS.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def fetch() -> None:
    for url in sorted({row["url"] for row in _rows()}):
        try:
            raw.fetch(SOURCE, url, suffix=".html")
        except Exception as error:
            print(f"{url}: {error}")


def emit() -> Path:
    rows = _rows()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Party memberships of Turkish politicians since 2023 that Wikidata and the Assembly's roll do not record, from the parties and the press",
            polity="TR",
            kind="edit",
        )
        parties = dict(conn.execute("select value, entity_id from external_id where scheme = 'tr.party'").fetchall())
        people = {
            f"{scheme}:{value}": person
            for scheme, value, person in conn.execute(
                """select x.scheme, x.value, x.entity_id from external_id x join person p on p.id = x.entity_id
                   where x.scheme in ('wikidata', 'tr.ysk_local.person', 'tr.person')"""
            ).fetchall()
        }
        people |= {
            f"contest:{value}": person
            for value, person in conn.execute(
                """select x.value, cd.person_id from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.is_default
                   join outcome o on o.result_set_id = r.id and o.kind = 'elected'
                   join candidacy cd on cd.option_id = o.option_id
                   where x.scheme = 'tr.contest' and x.value like '2024-03-31:%%'"""
            ).fetchall()
        }
        for (scheme, value), (into_scheme, into_value) in MERGES:
            cs.merge(people[f"{scheme}:{value}"], people[f"{into_scheme}:{into_value}"])
        sources = {}
        for row in rows:
            key = (row["publisher"], row["title"])
            if key not in sources:
                sources[key] = cs.source(*key)
                cs.add(
                    "source",
                    id=sources[key],
                    kind=row["kind"],
                    publisher=row["publisher"],
                    title=row["title"],
                    url=row["url"],
                    license="All rights reserved; cited, not reproduced",
                )
                artifact = raw.cached(SOURCE, row["url"])
                if artifact is not None:
                    cs.artifact(artifact, sources[key])
            cs.add(
                "person_party_membership",
                person_id=people[row["who"]],
                party_id=parties[row["party"]] if row["party"] else None,
                valid_from=date.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
                valid_from_precision=row["from_precision"],
                valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
                valid_to_precision=row["to_precision"] or "day",
                source_id=sources[key],
            )
        assembly = cs.source(
            "Türkiye Büyük Millet Meclisi",
            "Türkiye Büyük Millet Meclisi: Milletvekilleri (dönem listeleri ve özgeçmiş sayfaları)",
        )
        for person, party, start, start_precision, end, end_precision in conn.execute(
            """select t.person_id, a.party_id, a.valid_from, a.valid_from_precision, a.valid_to, a.valid_to_precision
               from tenure t join changeset c on c.id = t.changeset_id
               join tenure_affiliation a on a.tenure_id = t.id and a.kind = 'party'
               where c.pipeline = 'tr.ysk' and t.from_date >= '2023-06-01' and t.to_date is null
                 and t.person_id in (
                   select t2.person_id from tenure t2 join changeset c2 on c2.id = t2.changeset_id
                   join tenure_affiliation a2 on a2.tenure_id = t2.id and a2.kind = 'party'
                   where c2.pipeline = 'tr.ysk' and t2.from_date >= '2023-06-01' and t2.to_date is null
                     and a2.valid_from is distinct from t2.from_date)
                 and not exists (select from person_party_membership m join changeset mc on mc.id = m.changeset_id
                                 where m.person_id = t.person_id and mc.pipeline <> %s)""",
            [SOURCE],
        ).fetchall():
            cs.add(
                "person_party_membership",
                person_id=person,
                party_id=party,
                valid_from=start,
                valid_from_precision=start_precision,
                valid_to=end,
                valid_to_precision=end_precision,
                source_id=assembly,
            )
        staged = {
            (row["person_id"], row["party_id"], row["valid_from"], row["valid_to"])
            for row in cs.rows.get("person_party_membership", [])
        }
        for person, party, start, end in conn.execute(
            """select m.person_id, m.party_id, m.valid_from, m.valid_to from person_party_membership m
               join changeset c on c.id = m.changeset_id where c.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if (person, party, start, end) not in staged:
                cs.add(
                    "membership_drop",
                    person_id=person,
                    party_id=party,
                    valid_from=start,
                    valid_to=end,
                    reason="Wikidata now holds this membership",
                )
        overlaps = conn.execute(
            """select distinct a.person_id from person_party_membership a
               join person_party_membership b on b.person_id = a.person_id and b.party_id is distinct from a.party_id
                 and a.valid && b.valid
               join changeset c on c.id = a.changeset_id and c.pipeline = %s""",
            [SOURCE],
        ).fetchall()
        print(f"{len(overlaps)} people with this pipeline's membership overlapping another party's")
        return cs.write()
