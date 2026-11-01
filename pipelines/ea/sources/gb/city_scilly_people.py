from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import Connection, connect

SOURCE = "gb.city_scilly_people"
VERSION = "1"

NICKNAMES = {
    "steve": "stephen",
    "fran": "frances",
    "andy": "andrew",
    "tim": "timothy",
    "dan": "daniel",
    "mollie": "molly",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Isles of Scilly councillors who stood in more than one election joined to one person",
            polity="GB",
        )
        join(conn, cs)
        return cs.write()


def join(conn: Connection, cs: Changeset) -> None:
    rows = conn.execute(
        """
        select p.id, n.text, c.id
        from candidacy ca
        join contest_option co on co.id = ca.option_id
        join contest c on c.id = co.contest_id
        join person p on p.id = ca.person_id
        join name n on n.entity_id = p.id and n.is_preferred
        where c.body_id in (select entity_id from name where text = 'Isles of Scilly Council')
        """
    ).fetchall()
    groups: dict[tuple[str, str], dict[UUID, list[UUID]]] = {}
    for person, printed, contest in rows:
        words = printed.lower().replace("-", " ").split()
        if len(words) < 2:
            continue
        given = NICKNAMES.get(words[0], words[0])
        surname = " ".join(words[-2:]) if words[-2] == "dorrien" else words[-1]
        groups.setdefault((given, surname), {}).setdefault(person, []).append(contest)
    for members in groups.values():
        if len(members) < 2:
            continue
        contests = [contest for seen in members.values() for contest in seen]
        if len(contests) != len(set(contests)):
            continue
        survivor = max(members, key=lambda person: (len(members[person]), str(person)))
        for person in members:
            cs.merge(person, survivor)
