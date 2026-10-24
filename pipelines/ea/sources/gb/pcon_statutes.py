from pathlib import Path
from uuid import uuid5

import polars as pl

from ea.changeset import NAMESPACE, Changeset
from ea.store import connect

SOURCE = "gb.pcon_statutes"
VERSION = "1"
LINKS = Path(__file__).with_name("pcon_statutes.tsv")
OGL = ("Open Government Licence v3.0", "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/")
STATUTES = {
    "1868": (
        "Representation of the People Acts 1867 and 1868 and Boundary Act 1868, schedules",
        "https://www.legislation.gov.uk/ukpga/Vict/30-31/102/pdfs/ukpga_18670102_en.pdf",
        *OGL,
        (
            "Representation of the People Act 1867 (30 & 31 Vict. c. 102), "
            "Boundary Act 1868 (31 & 32 Vict. c. 46, https://www.legislation.gov.uk/ukpga/Vict/31-32/46/pdfs/ukpga_18680046_en.pdf), "
            "Representation of the People (Scotland) Act 1868 (31 & 32 Vict. c. 48, https://www.legislation.gov.uk/ukpga/Vict/31-32/48/enacted), "
            "Representation of the People (Ireland) Act 1868 (31 & 32 Vict. c. 49, https://www.legislation.gov.uk/ukpga/Vict/31-32/49/pdfs/ukpga_18680049_en.pdf), "
            "read against the Parliamentary Boundaries Act 1832 (2 & 3 Will. 4 c. 64, https://www.legislation.gov.uk/ukpga/Will4/2-3/64/enacted)"
        ),
    ),
    "1885": (
        "Redistribution of Seats Act 1885 (48 & 49 Vict. c. 23), schedules",
        "https://archive.org/details/india.history.resource.106075",
        "Public domain",
        None,
        "The Public General Statutes for 48 & 49 Vict., read against the Acts of 1832, 1867, and 1868",
    ),
}
NOTES = (
    "Exact crosswalks between the gb.pcon boundary sets of 1832 and 1868, and of 1868 and 1885, keyed by hand from the "
    "statutes' schedules, never from shapes. A link with weight 1 means the Act shows the whole of one constituency lying "
    "inside the other; each link's citation names the section or schedule. A direction the Act does not prove is left out, "
    "so a borough the Act extends links only from old to new, and a division of an old county or borough links only from "
    "new to old. Constituencies an Act redraws without starting from the old boundary, and counties into which a borough "
    "ceases or is cut back, get no link"
)


def emit() -> Path:
    links = pl.read_csv(
        LINKS,
        separator="\t",
        schema_overrides={"from": pl.Utf8, "to": pl.Utf8, "from_code": pl.Utf8, "to_code": pl.Utf8},
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Exact crosswalks between the UK Parliament constituencies of 1832, 1868, and 1885, wherever the "
            "Reform Acts' and the Redistribution of Seats Act's schedules show one constituency lying wholly inside another",
            polity="GB",
        )
        sets = {
            vintage: (id, valid)
            for id, vintage, valid in conn.execute(
                "select id, vintage, valid from boundary_set where scheme_code = 'gb.pcon' and vintage in ('1832', '1868', '1885')"
            ).fetchall()
        }
        sources = {}
        for later, (title, url, license, license_url, notes) in STATUTES.items():
            sources[later] = cs.source("Parliament of the United Kingdom", title)
            cs.add(
                "source",
                id=sources[later],
                kind="official_body",
                publisher="Parliament of the United Kingdom",
                title=title,
                url=url,
                license=license,
                license_url=license_url,
                notes=f"{notes}. {NOTES}",
            )
        areas = {}
        for vintage, (_, valid) in sets.items():
            for code, k, id in conn.execute(
                """select c.code, a.k, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'gb.pcon' and (c.valid is null or c.valid && %s::daterange)""",
                [valid],
            ).fetchall():
                if areas.setdefault((vintage, code), (k, id)) != (k, id):
                    raise ValueError(f"{vintage} {code} names two areas")
        for (source, target), group in links.group_by(["from", "to"], maintain_order=True):
            later = max(source, target)
            crosswalk = uuid5(NAMESPACE, f"{SOURCE}\x1f{sets[source][0]}\x1f{sets[target][0]}\x1f{VERSION}")
            cs.add(
                "crosswalk",
                id=crosswalk,
                from_kind="boundary_set",
                from_boundary_set_id=sets[source][0],
                to_boundary_set_id=sets[target][0],
                method="exact",
            )
            rows = []
            for from_code, to_code, basis in group.select("from_code", "to_code", "basis").rows():
                (from_k, from_id), (to_k, _) = areas[source, from_code], areas[target, to_code]
                rows.append((from_k, to_k))
                cs.add(
                    "citation",
                    entity_id=from_id,
                    source_id=sources[later],
                    locator={"crosswalk": str(crosswalk), "to": to_code, "basis": basis},
                )
            weights = pl.DataFrame(rows, schema={"from_k": pl.Int32, "to_k": pl.Int32}, orient="row")
            if weights["from_k"].is_duplicated().any():
                raise ValueError(f"{source} -> {target}: a constituency links to two")
            cs.frame(
                "crosswalk_weight",
                weights.select(
                    crosswalk_id=pl.lit(str(crosswalk)),
                    from_k="from_k",
                    to_k="to_k",
                    weight=pl.lit(1).cast(pl.Decimal(10, 6)),
                    exact=pl.lit(True),
                ),
            )
            print(f"gb.pcon {source} -> {target}: {weights.height} links")
        return cs.write()
