import re
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "tr.muhtar_1963_links"
VERSION = "1"


def fold(text: str) -> str:
    text = re.sub(r"\s+(Mah\.|Mahallesi|Köyü)$", "", text.strip()).replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-z]", "", text.translate(str.maketrans("ığüşöçâîû", "iguoocaiu")))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Approximate places for the köys of Turkey's 1963 muhtar elections: each köy matched one to one "
            "by name to a 2024 köy or mahalle in its 1963 ilçe or an ilçe split from it, or, where the name is "
            "unique, anywhere in its province",
            polity="TR",
        )
        units = conn.execute(
            """select u.id, u.code, n.text, u.area_id, a.scheme_code from reporting_unit u
               join area a on a.id = u.area_id
               join name n on n.entity_id = u.id and n.is_preferred
               where u.scheme_code = 'tr.mahalle' and u.code like '1963:%'"""
        ).fetchall()
        places = conn.execute(
            """select a.id, p.parent_id, n.text from area a join area_parent p on p.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred and n.lang = 'tr'
               where a.scheme_code = 'tr.mahalle'"""
        ).fetchall()
        province = dict(
            conn.execute(
                """select p.area_id, p.parent_id from area_parent p join area a on a.id = p.area_id
                   where a.scheme_code = 'tr.ilce' and p.valid_to is null"""
            ).fetchall()
        )
        children = defaultdict(set)
        for before, after in conn.execute(
            """select f.area_id, t.area_id from area_event e
               join area_event_member f on f.event_id = e.id and f.role = 'from'
               join area_event_member t on t.event_id = e.id and t.role = 'to'
               join area a on a.id = f.area_id and a.scheme_code = 'tr.ilce'
               where e.kind in ('created', 'split', 'renamed') and f.area_id <> t.area_id"""
        ).fetchall():
            children[before].add(after)

        def family(area: object) -> set:
            seen, todo = {area}, [area]
            while todo:
                for child in children[todo.pop()] - seen:
                    seen.add(child)
                    todo.append(child)
            return seen

        in_ilce = defaultdict(list)
        in_il = defaultdict(list)
        for area, parent, text in places:
            in_ilce[parent].append((area, fold(text)))
            in_il[province.get(parent)].append((area, fold(text)))

        links: dict = {}
        groups = defaultdict(list)
        plates = defaultdict(list)
        plate_ils = defaultdict(set)
        for unit, code, text, area, scheme in units:
            plate = code.split(":")[1]
            groups[area, scheme].append((unit, fold(text)))
            plates[plate].append((unit, fold(text)))
            plate_ils[plate].add(area if scheme == "tr.il" else province.get(area))
        for (area, scheme), members in groups.items():
            pool = in_il[area] if scheme == "tr.il" else [p for ilce in family(area) for p in in_ilce[ilce]]
            index = defaultdict(list)
            for place, key in pool:
                index[key].append(place)
            seen = Counter(key for _, key in members)
            for unit, key in members:
                if key and seen[key] == 1 and len(index[key]) == 1:
                    links[unit] = (index[key][0], "ilce")
        taken = {place for place, _ in links.values()}
        for plate, members in plates.items():
            index = defaultdict(list)
            for il in plate_ils[plate]:
                for place, key in in_il[il]:
                    index[key].append(place)
            seen = Counter(key for _, key in members)
            for unit, key in members:
                if unit not in links and key and seen[key] == 1 and len(index[key]) == 1 and index[key][0] not in taken:
                    links[unit] = (index[key][0], "province")
        twice = {place for place, count in Counter(place for place, _ in links.values()).items() if count > 1}
        links = {unit: link for unit, link in links.items() if link[0] not in twice}
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                [{"unit_id": str(unit), "area_id": str(place)} for unit, (place, _) in links.items()],
                schema={"unit_id": pl.String, "area_id": pl.String},
            ).with_columns(valid_from=pl.lit(None, pl.Date), valid_to=pl.lit(None, pl.Date), approximate=pl.lit(True)),
        )
        report = pl.DataFrame(
            [
                {
                    "plate": code.split(":")[1],
                    "code": code,
                    "name": text,
                    "linked": unit in links,
                    "pass": links[unit][1] if unit in links else None,
                }
                for unit, code, text, _, _ in units
            ]
        ).sort("plate", "code")
        report.write_csv(DATA / "parsed" / f"{SOURCE}.csv")
        for plate, total, linked in (
            report.group_by("plate").agg(pl.len(), pl.col("linked").sum()).sort("plate").iter_rows()
        ):
            print(f"{plate}: {linked} of {total}")
        print(
            f"{len(links)} of {len(units)} köys linked, {sum(1 for _, how in links.values() if how == 'province')} "
            f"by a name unique in the province; {len(twice)} places claimed twice left out"
        )
        return cs.write()
