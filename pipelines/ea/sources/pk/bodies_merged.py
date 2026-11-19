from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.sources.pk import ecp, ps2018
from ea.store import connect

SOURCE = "pk.bodies_merged"
VERSION = "1"

# The 2018 polling-station package minted its own assemblies and first-past-the-post system; each merges into the
# Election Commission package's entity. Khyber Pakhtunkhwa's assembly is the North-West Frontier Province's, renamed
# in 2010.
SURVIVORS = {"NA": "NA", "PP": "PP", "PS": "PS", "PK": "PF", "PB": "PB"}


def pairs() -> list[tuple[str, str]]:
    found = [(ps2018.ASSEMBLIES[theirs].body, ecp.ASSEMBLIES[ours].body) for theirs, ours in SURVIVORS.items()]
    return [*found, (ps2018.SYSTEM, ecp.SYSTEM)]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Pakistan's National Assembly, provincial assemblies, and first-past-the-post system, minted "
            "twice, merged into one record each",
            polity="PK",
        )
        for duplicate, survivor in pairs():
            if conn.execute("select from entity_merge where entity_id = %s", [duplicate]).fetchone() is None:
                cs.merge(UUID(duplicate), UUID(survivor))
        return cs.write()
