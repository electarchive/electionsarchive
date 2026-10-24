import csv
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "gb.euc"
VERSION = "1"
ELECTIONS = {
    1979: date(1979, 6, 7),
    1984: date(1984, 6, 14),
    1989: date(1989, 6, 15),
    1994: date(1994, 6, 9),
}
END = date(1999, 6, 10)
REPLACED = {1984: {"ENG", "WLS", "SCT"}, 1994: {"ENG", "WLS"}}
ORDERS = {
    "1978/1903": "The European Assembly Constituencies (England) Order 1978",
    "1978/1904": "The European Assembly Constituencies (Wales) Order 1978",
    "1978/1911": "The European Assembly Constituencies (Scotland) Order 1978",
    "1984/544": "The European Assembly Constituencies (England) Order 1984",
    "1984/545": "The European Assembly Constituencies (Wales) Order 1984",
    "1984/548": "The European Assembly Constituencies (Scotland) Order 1984",
    "1989/486": "The European Parliamentary Constituencies (England) (Miscellaneous Changes) Order 1989",
    "1989/487": "The European Parliamentary Constituencies (Wales) (Miscellaneous Changes) Order 1989",
    "1989/494": "The European Parliamentary Constituencies (Scotland) (Miscellaneous Changes) Order 1989",
    "1992/236": "The European Parliamentary Constituencies (Scotland) (Miscellaneous Changes) Order 1992",
    "1994/427": "The European Parliamentary Constituencies (England) Order 1994",
    "1994/428": "The European Parliamentary Constituencies (Wales) Order 1994",
}


def url(order: str) -> str:
    year, number = order.split("/")
    if year in ("1992", "1994"):
        return f"https://www.legislation.gov.uk/uksi/{order}/made/data.xml"
    return f"https://www.legislation.gov.uk/uksi/{order}/pdfs/uksi_{year}{int(number):04d}_en.pdf"


def fetch() -> None:
    for order in ORDERS:
        try:
            raw.cached(SOURCE, url(order))
        except LookupError:
            raw.fetch(SOURCE, url(order), suffix=".xml" if url(order).endswith(".xml") else ".pdf")


def emit() -> Path:
    with open(Path(__file__).with_name("euc.tsv"), newline="") as handle:
        rows = [{**row, "era": int(row["era"])} for row in csv.DictReader(handle, delimiter="\t")]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Great Britain's European Parliament constituencies of 1979–1994, named as in the constituency Orders "
            "of 1978, 1984, and 1994 and their 1989 and 1992 changes, without shapes",
            polity="GB",
        )
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = 'gb.euc'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.euc",
                polity_id=gb,
                kind="electoral_district",
                rank=1,
                noun="European Parliament constituency",
            )
        sources = {}
        for order, title in ORDERS.items():
            sources[order] = cs.source("UK Government", f"{title} (SI {order})")
            cs.add(
                "source",
                id=sources[order],
                kind="official_body",
                publisher="UK Government",
                title=f"{title} (SI {order})",
                url=f"https://www.legislation.gov.uk/uksi/{order}/made",
                license="Open Government Licence v3.0",
                license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            )
            cs.artifact(raw.cached(SOURCE, url(order)), sources[order])
        countries = {code: cs.area("gb.country", code) for code in ("ENG", "WLS", "SCT")}
        if any(area in cs.minted for area in countries.values()):
            raise ValueError("publish gb.umit first: the countries must exist")
        spans = []
        for row in rows:
            later = [
                era
                for era in ELECTIONS
                if era > row["era"]
                and (
                    row["country"] in REPLACED.get(era, set())
                    or any(r["era"] == era and (r["country"], r["name"]) == (row["country"], row["name"]) for r in rows)
                )
            ]
            start, end = ELECTIONS[row["era"]], ELECTIONS[later[0]] if later else END
            spans.append((start, end))
            code = f"{row['era']}:{slug(row['name'])}"
            area = cs.area("gb.euc", code, start)
            unit = cs.unit("gb.euc", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.euc")
                cs.add("name", entity_id=area, text=row["name"], lang="en", kind="official", is_preferred=True)
            cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
            cs.add("area_parent", area_id=area, parent_id=countries[row["country"]], valid_from=start, valid_to=end)
            cs.add("citation", entity_id=area, source_id=sources[row["order"]])
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=gb,
                    scheme_code="gb.euc",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=start,
                    valid_to=end,
                )
                cs.add("name", entity_id=unit, text=row["name"], lang="en", kind="official", is_preferred=True)
        counts = {era: sum(1 for start, end in spans if start <= day < end) for era, day in ELECTIONS.items()}
        if counts != {1979: 78, 1984: 78, 1989: 78, 1994: 84}:
            raise ValueError(f"constituencies in force per election: {counts}")
        return cs.write()
