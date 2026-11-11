from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.la_special_alias"
VERSION = "1"

ALIASES = {
    "22059:eden-fellowship-volunteer-fire-district": "22059:eden-fellowship-vol-fire",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge Louisiana special districts that the source spells two ways into one polity, with its area and reporting unit",
            polity="US",
            kind="correction",
        )
        for alias, code in ALIASES.items():
            duplicate = cs.key("us.polity", f"special:{alias}")
            survivor = cs.key("us.polity", f"special:{code}")
            if duplicate == survivor:
                continue
            cs.merge(duplicate, survivor)
            cs.merge(cs.area("us.special_district", alias), cs.area("us.special_district", code))
            cs.merge(cs.unit("us.special_district", alias), cs.unit("us.special_district", code))
        return cs.write()
