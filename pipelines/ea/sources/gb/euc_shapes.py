import csv
from pathlib import Path

from ea.changeset import Changeset, slug
from ea.sources.gb import euc
from ea.store import connect

SOURCE = "gb.euc_shapes"
VERSION = "1"
MEMBERS = Path(__file__).with_name("euc_members.tsv")
QUALITY = ["official", "osm", "academic", "digitized", "synthetic"]
NOTES = (
    "Each Order defines a European constituency as a list of Westminster constituencies, so the shape is the union "
    "of those constituencies' gb.pcon shapes on the European constituency's first polling day. The lists come from "
    "the Orders on legislation.gov.uk: the 1989, 1992, and 1994 Orders as text, the 1978 and 1984 Orders by OCR of "
    "the printed scans, every name matched to a Westminster constituency in force, each used exactly once per election."
)


def emit() -> Path:
    with Path(euc.__file__).with_name("euc.tsv").open() as handle:
        eras = {(row["order"], row["name"]): row["era"] for row in csv.DictReader(handle, delimiter="\t")}
    with MEMBERS.open() as handle:
        members = [
            (f"{eras[(row['order'], row['name'])]}:{slug(row['name'])}", row["pcon"], row["order"])
            for row in csv.DictReader(handle, delimiter="\t")
        ]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Shapes for the GB European Parliament constituencies of 1979–1994, as unions of the Westminster "
            "constituencies each Order lists",
            polity="GB",
        )
        sources = {order: cs.source("UK Government", f"{title} (SI {order})") for order, title in euc.ORDERS.items()}
        if cs.minted & set(sources.values()):
            raise ValueError("publish gb.euc first: the Orders must exist as sources")
        conn.execute("create temp table member (code text, pcon text, ord text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy member from stdin") as copy:
            for row in members:
                copy.write_row(row)
        rows = conn.execute(
            """select e.id, min(m.ord), lower(ec.valid), upper(ec.valid), count(distinct p.id), count(distinct m.pcon),
                      max(array_position(%s::text[], g.quality::text)),
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(g.geom)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_union(g.geom))), 'hex')
               from member m
               join area_code ec on ec.code = m.code
               join area e on e.id = ec.area_id and e.scheme_code = 'gb.euc'
               join area_code pc on pc.code = m.pcon
               join area p on p.id = pc.area_id and p.scheme_code = 'gb.pcon'
               join area_geometry g on g.area_id = p.id and g.valid @> lower(ec.valid)
               group by e.id, ec.valid""",
            [QUALITY],
        ).fetchall()
        expected = len({code for code, _, _ in members})
        if len(rows) != expected:
            raise ValueError(f"{len(rows)} shaped of {expected} constituencies")
        for area, order, start, end, found, listed, quality, geom, point in rows:
            if found != listed:
                raise ValueError(f"{area}: {found} of {listed} Westminster shapes on {start}")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality=QUALITY[quality - 1],
                source_id=sources[order],
            )
            cs.add("note", entity_id=area, kind="context", body_md=NOTES)
        plans = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'gb.euc'").fetchall())
        days = sorted(euc.ELECTIONS.items())
        for (year, day), end in zip(days, [*[d for _, d in days[1:]], euc.END], strict=True):
            cs.add(
                "boundary_set",
                **({"id": plans[str(year)]} if str(year) in plans else {}),
                scheme_code="gb.euc",
                vintage=str(year),
                valid_from=day,
                valid_to=end,
                as_of=day,
            )
        return cs.write()
