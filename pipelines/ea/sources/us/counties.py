import re
from pathlib import Path

from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.counties"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Counties and county-equivalents as local polities, with the county areas as their territory",
            polity="US",
        )
        states = dict(conn.execute("select iso_code, id from polity where iso_code like 'US-%'").fetchall())
        seen: set[str] = set()
        for code, official in conn.execute(
            """select c.code, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.kind = 'official'
               where a.scheme_code = 'us.county' order by c.code"""
        ).fetchall():
            text, usps = official.rsplit(", ", 1)
            if f"US-{usps}" not in states:
                continue
            city = re.fullmatch(r"(.+) city", text)
            text = f"City of {city[1]}" if city else text.removesuffix(" Municipio")
            path = f"us/{usps.lower()}/{slug(text)}"
            if path in seen:
                raise ValueError(f"duplicate slug {path}")
            seen.add(path)
            polity = cs.key("us.polity", f"county:{code}")
            cs.add(
                "polity",
                id=polity,
                kind="municipality" if city or usps == "PR" else "county",
                iso_code=None,
            )
            cs.add("name", entity_id=polity, text=text, lang="en", kind="official", is_preferred=True)
            cs.add("polity_parent", polity_id=polity, parent_id=states[f"US-{usps}"])
            cs.add("polity_territory", polity_id=polity, area_id=cs.area("us.county", code))
            cs.add("slug", path=path, entity_id=polity)
        return cs.write()
