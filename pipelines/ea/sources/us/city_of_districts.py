from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.sources.us.enr_clarity_twins import LIVE
from ea.store import connect

SOURCE = "us.city_of_districts"
VERSION = "3"
PIPELINES = ["us.co_tabor_2026", "us.mo_measures_2026", "us.nov3_manual_2026", "us.mcd_towns", "us.nj_clerks"]
PREFIX = r"^(City|Town|Village|Borough) of "
PREFERRED = "(select text from name n where n.entity_id = {0} and n.is_preferred and n.lang = 'en' limit 1)"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge special districts named for a city, town, or village into the municipality of the same name and state",
            polity="US",
            kind="correction",
        )
        pairs = conn.execute(
            f"""select distinct d.id, t.id, {PREFERRED.format("d.id")} from polity d
                join changeset g on g.id = d.changeset_id and g.pipeline = any(%s)
                join slug ds on ds.entity_id = d.id and ds.is_current
                join name dn on dn.entity_id = d.id and dn.is_preferred and dn.lang = 'en'
                join slug ms on ms.entity_id <> d.id and ms.is_current and split_part(ms.path, '/', 2) = split_part(ds.path, '/', 2)
                  and split_part(ms.path, '/', 4) = ''
                join polity t on t.id = ms.entity_id and t.kind = 'municipality'
                join name mn on mn.entity_id = t.id and mn.is_preferred and mn.lang = 'en'
                  and mn.text = regexp_replace(dn.text, %s, '')
                where d.kind in ('special_district', 'municipality') and d.id <> t.id and dn.text ~ %s and {LIVE.format("d")} and {LIVE.format("t")}
                order by 1""",
            [PIPELINES, PREFIX, PREFIX],
        ).fetchall()
        merged = 0
        for district, town, label in pairs:
            base = label.split(" of ", 1)[1]
            elections = {
                day: election
                for election, day in conn.execute(
                    f"select e.id, e.date_from from election e where e.polity_id = %s and {LIVE.format('e')}", [town]
                ).fetchall()
            }
            for election, day in conn.execute(
                f"select e.id, e.date_from from election e where e.polity_id = %s and {LIVE.format('e')}", [district]
            ).fetchall():
                kept = elections.get(day)
                if kept is None:
                    continue
                survivors: dict[str, UUID] = {
                    text.partition(": ")[2]: contest
                    for contest, text in conn.execute(
                        f"select c.id, {PREFERRED.format('c.id')} from contest c where c.election_id = %s and {LIVE.format('c')}",
                        [kept],
                    ).fetchall()
                }
                for contest, text in conn.execute(
                    f"select c.id, {PREFERRED.format('c.id')} from contest c where c.election_id = %s and {LIVE.format('c')}",
                    [election],
                ).fetchall():
                    into = survivors.get(text.partition(": ")[2])
                    if into is not None:
                        cs.merge(contest, into)
                cs.merge(election, kept)
            cs.merge(district, town)
            for table, key in (("polity_territory", "area_id"), ("reporting_unit", "id")):
                pair = conn.execute(
                    f"select a.{key}, b.{key} from {table} a, {table} b where a.polity_id = %s and b.polity_id = %s limit 1",
                    [district, town],
                ).fetchone()
                if pair is not None:
                    cs.merge(pair[0], pair[1])
            merged += 1
            print("merged", label, "into", base)
        print(f"{merged} special districts merged into municipalities")
        return cs.write()
