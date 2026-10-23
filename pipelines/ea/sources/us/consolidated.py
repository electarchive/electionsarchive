from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import connect

SOURCE = "us.consolidated"
VERSION = "1"
URL = "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt"
PUBLISHER = "U.S. Census Bureau"
TITLE = "2020 National County and County Equivalent Codes"
PAIRS = {
    "0203000": ("02020", date(1975, 9, 16), "day"),
    "0667000": ("06075", date(1856, 7, 1), "day"),
    "0820000": ("08031", date(1902, 11, 15), "day"),
    "1150000": ("11001", date(1871, 2, 21), "day"),
    "1235000": ("12031", date(1968, 10, 1), "day"),
    "1303440": ("13059", date(1991, 1, 14), "day"),
    "1304204": ("13245", date(1996, 1, 1), "day"),
    "1319000": ("13215", date(1971, 1, 1), "day"),
    "2036000": ("20209", date(1997, 10, 1), "day"),
    "2146027": ("21067", date(1974, 1, 1), "day"),
    "2148000": ("21111", date(2003, 1, 6), "day"),
    "2205000": ("22033", date(1949, 1, 1), "day"),
    "2240735": ("22055", date(1996, 1, 1), "year"),
    "2255000": ("22071", date(1874, 1, 1), "year"),
    "3209700": ("32510", date(1969, 7, 1), "day"),
    "4260000": ("42101", date(1854, 2, 2), "day"),
    "4752006": ("47037", date(1963, 4, 1), "day"),
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".txt")


def parse() -> None:
    classes = {
        f"{fields[1]}{fields[2]}": fields[5]
        for fields in (line.split("|") for line in raw.cached(SOURCE, URL).path.read_text().splitlines()[1:])
    }
    for place, (county, _, _) in PAIRS.items():
        if classes.get(county) not in ("H6", "C7"):
            raise ValueError(f"{place}: {county} is {classes.get(county)}, not a consolidated county")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Consolidated city-counties linked to their county polities, paired by the Census Bureau's county "
            "class codes",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public domain",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        polities = {
            (scheme, code): polity
            for scheme, code, polity in conn.execute(
                """select a.scheme_code, c.code, t.polity_id from area a join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id join polity p on p.id = t.polity_id
                   where (a.scheme_code, p.kind) in (('us.place', 'municipality'), ('us.county', 'county'))
                     and c.code = any(%s)""",
                [[*PAIRS, *(county for county, _, _ in PAIRS.values())]],
            ).fetchall()
        }
        for place, (county, day, precision) in PAIRS.items():
            city, whole = polities[("us.place", place)], polities[("us.county", county)]
            cs.add(
                "polity_relation",
                id=uuid5(NAMESPACE, f"polity_relation\x1f{city}\x1fconsolidated_with\x1f{whole}"),
                from_polity_id=city,
                to_polity_id=whole,
                kind="consolidated_with",
                date=day,
                date_precision=precision,
                source_id=source,
            )
        return cs.write()
