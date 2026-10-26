from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "de.union"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The CDU/CSU as one alliance in every German election where both parties stood",
            polity="DE",
        )
        parties = dict(
            conn.execute(
                """select n.text, p.id from party p join name n on n.entity_id = p.id and n.text in ('CDU', 'CSU')
                   join changeset c on c.id = p.changeset_id where c.pipeline like 'de.%%'
                     and not exists (select from entity_merge m where m.entity_id = p.id)"""
            ).fetchall()
        )
        if set(parties) != {"CDU", "CSU"}:
            raise ValueError(f"expected one CDU and one CSU party, found {sorted(parties)}")
        elections = conn.execute(
            """select e.id from election e join contest k on k.election_id = e.id
               join contest_option o on o.contest_id = k.id
               where o.party_id = any(%s) group by e.id having count(distinct o.party_id) = 2 order by e.id""",
            [list(parties.values())],
        ).fetchall()
        for (election,) in elections:
            alliance = cs.key("de.union.alliance", str(election))
            cs.add("alliance", id=alliance, kind="federation", election_id=election)
            cs.add("name", entity_id=alliance, text="CDU/CSU", lang="de", kind="official", is_preferred=True)
            cs.add("name", entity_id=alliance, text="CDU/CSU", lang="en", kind="official", is_preferred=True)
            cs.add("name", entity_id=alliance, text="Union", lang="de", kind="short", is_preferred=False)
            for party in parties.values():
                cs.add("alliance_member", alliance_id=alliance, party_id=party)
        return cs.write()
