from datetime import date
from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.nations"
VERSION = "1"
NATIONS = (
    ("ENG", "England", "england", None),
    ("SCT", "Scotland", "scotland", None),
    ("WLS", "Wales", "wales", None),
    ("NIR", "Northern Ireland", "northern-ireland", date(1921, 6, 7)),
)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="England, Scotland, Wales, and Northern Ireland as polities under the United Kingdom",
            polity="GB",
        )
        gb = cs.polity("GB")
        for code, label, path, since in NATIONS:
            area = cs.area("gb.country", code)
            if area in cs.minted:
                raise ValueError(f"publish gb.parliament and gb.umit before gb.nations: no {code} area")
            polity = cs.polity(f"GB-{code}")
            cs.add("polity", id=polity, kind="region", iso_code=f"GB-{code}")
            cs.add("name", entity_id=polity, text=label, lang="en", kind="official", is_preferred=True)
            cs.add("polity_parent", polity_id=polity, parent_id=gb, valid_from=since)
            cs.add("polity_territory", polity_id=polity, area_id=area, valid_from=since)
            cs.add("slug", path=f"gb/{path}", entity_id=polity)
        return cs.write()
