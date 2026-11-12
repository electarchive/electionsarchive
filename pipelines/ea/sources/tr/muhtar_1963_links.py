import re
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "tr.muhtar_1963_links"
VERSION = "4"
RENAMES = Path(__file__).with_name("koy_renames_1968.tsv")
KOYLERIMIZ_1933 = Path(__file__).with_name("koylerimiz_1933.tsv")
ANCHORS = Path(__file__).with_name("koylerimiz_1933_links.tsv")
KOYLERIMIZ_URL = (
    "https://acikerisim.tbmm.gov.tr/server/api/core/bitstreams/4fa70aa0-9537-4c18-b776-fb75a93b2bdc/content"
)
RENAMES_URL = "https://acikerisim.tbmm.gov.tr/server/api/core/bitstreams/42c011d2-44d8-498e-8353-41a658b94ba9/content"


def fold(text: str) -> str:
    text = re.sub(r"\s+(Mah\.|Mahallesi|Köyü)$", "", text.strip()).replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-z]", "", text.translate(str.maketrans("ığüşöçâîû", "iguoocaiu")))


def title(text: str) -> str:
    words = text.replace("İ", "i").replace("I", "ı").lower().split()
    return " ".join(word[:1].replace("i", "İ").replace("ı", "I").upper() + word[1:] for word in words)


def readings(name: str) -> set[str]:
    key = fold(name)
    return {key} | {
        key[:i] + new + key[i + 1 :] for old, new in (("m", "in"), ("g", "s")) for i, c in enumerate(key) if c == old
    }


def same_ilce(a: str, b: str) -> bool:
    from ea.sources.tr.koylerimiz_1933 import key

    short, long = sorted((key(a), key(b)), key=len)
    return short == long or (len(short) > 3 and (long.endswith(short) or long.startswith(short)))


def parse() -> Path:
    from ea.sources.tr.koylerimiz_1933 import PROVINCES, key

    with connect() as conn:
        units = conn.execute(
            """select u.code, n.text, x.scheme || ':' || x.value, an.text from reporting_unit u
               join name n on n.entity_id = u.id and n.is_preferred
               left join reporting_unit_area l on l.unit_id = u.id and l.approximate
               left join area a on a.id = l.area_id and a.scheme_code = 'tr.mahalle'
               left join name an on an.entity_id = a.id and an.is_preferred and an.lang = 'tr'
               left join lateral (
                 select scheme, value from external_id where entity_id = a.id
                 order by scheme = 'tr.tuik.koy' desc, scheme = 'tr.tuik.mahalle' desc, scheme limit 1
               ) x on true
               where u.scheme_code = 'tr.mahalle' and u.code like '1963:%'"""
        ).fetchall()
    book = pl.read_csv(KOYLERIMIZ_1933, separator="\t", infer_schema=False).fill_null("").to_dicts()
    by_name = defaultdict(list)
    for index, row in enumerate(book):
        for plate in PROVINCES.get(row["vilayet"], []):
            by_name[plate, key(row["koy"])].append(index)
    renamed = defaultdict(list)
    for row in pl.read_csv(RENAMES, separator="\t", infer_schema=False).iter_rows(named=True):
        renamed[row["plate"], key(row["new_name"])].append(row)
    named = Counter((code.split(":")[1], key(text)) for code, text, _, _ in units)
    picks, claims = {}, defaultdict(list)
    for code, text, _, _ in units:
        plate, ilce = code.split(":")[1:3]
        name = key(text)
        if not name:
            continue
        via, options = "name", by_name.get((plate, name), [])
        if not options:
            olds = {key(row["old_name"]) for row in renamed[plate, name] if same_ilce(row["ilce"], ilce)}
            if len(olds) == 1:
                via, options = "1968 rename", by_name.get((plate, olds.pop()), [])
        if len(options) > 1 or named[plate, name] > 1:
            options = [i for i in options if book[i]["kaza"] and same_ilce(book[i]["kaza"], ilce)]
        if len(options) == 1:
            picks[code] = (options[0], via)
            claims[options[0]].append(code)
    rows = []
    for code, text, area, area_name in units:
        if code not in picks or len(claims[picks[code][0]]) != 1:
            continue
        index, via = picks[code]
        row = book[index]
        kaza = row["kaza"] and ("agrees" if same_ilce(row["kaza"], code.split(":")[2]) else "differs")
        rows.append(
            {
                "code": code,
                "name": text,
                "page": row["page"],
                "column": row["column"],
                "y": row["y"],
                "koy_1933": row["koy"],
                "vilayet_1933": row["vilayet"],
                "kaza_1933": row["kaza"],
                "mark": row["mark"],
                "via": via,
                "kaza": kaza,
                "name_1968": row["name_1968"],
                "area_2024": area or "",
                "area_name_2024": area_name or "",
            }
        )
    frame = pl.DataFrame(rows).sort("code")
    frame.write_csv(ANCHORS, separator="\t")
    print(
        f"{frame.height} of {len(units)} 1963 köys anchored one to one to a 1933 Köylerimiz row "
        f"({frame.filter(pl.col('via') == '1968 rename').height} through a 1968 rename; kaza "
        f"{dict(frame.group_by('kaza').len().iter_rows())}; "
        f"{frame.filter(pl.col('name_1968') != '').height} also read in the 1968 old names; "
        f"{frame.filter(pl.col('area_2024') != '').height} with a 2024 place)"
    )
    return ANCHORS


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Approximate places for the köys of Turkey's 1963 muhtar elections: each köy matched one to one "
            "by name to a 2024 köy or mahalle in its 1963 ilçe or an ilçe split from it, or, where the name is "
            "unique, anywhere in its province, or by its new name from the İçişleri Bakanlığı's Köylerimiz (1 March "
            "1968) where it was renamed after 1963",
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

        ilce_names = defaultdict(set)
        for area, text in conn.execute(
            """select a.id, n.text from area a join name n on n.entity_id = a.id where a.scheme_code = 'tr.ilce'"""
        ).fetchall():
            ilce_names[province.get(area)].add((fold(text), area))

        in_ilce = defaultdict(list)
        in_il = defaultdict(list)
        for area, parent, text in places:
            in_ilce[parent].append((area, fold(text)))
            in_il[province.get(parent)].append((area, fold(text)))

        links: dict = {}
        groups = defaultdict(list)
        plates = defaultdict(list)
        plate_ils = defaultdict(set)
        unit_ilce, plate_of = {}, {}
        for unit, code, text, area, scheme in units:
            plate = code.split(":")[1]
            unit_ilce[unit] = code.split(":")[2:4]
            plate_of[unit] = plate
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
        misread = {}

        def read_pass(members: list, index: dict, label: str) -> None:
            taken = {place for place, _ in links.values()}
            names = Counter(key for _, key in members)
            for unit, key in members:
                if unit in links or not key or names[key] != 1 or index[key]:
                    continue
                hits = {index[k][0]: k for k in readings(key) - {key} if len(index[k]) == 1 and names[k] == 0}
                if len(hits) == 1 and (place := next(iter(hits))) not in taken:
                    links[unit] = (place, label)
                    misread[unit] = hits[place]
                    taken.add(place)

        for (area, scheme), members in groups.items():
            pool = in_il[area] if scheme == "tr.il" else [p for ilce in family(area) for p in in_ilce[ilce]]
            index = defaultdict(list)
            for place, key in pool:
                index[key].append(place)
            read_pass(members, index, "ilce misread")
        for plate, members in plates.items():
            index = defaultdict(list)
            for il in plate_ils[plate]:
                for place, key in in_il[il]:
                    index[key].append(place)
            read_pass(members, index, "province misread")
        renames = defaultdict(list)
        for row in pl.read_csv(RENAMES, separator="\t", infer_schema=False).iter_rows(named=True):
            renames[row["plate"], fold(row["old_name"])].append(row)
        renamed_to = {}
        taken = {place for place, _ in links.values()}
        for (area, scheme), members in groups.items():
            ilces = {area} if scheme == "tr.il" else family(area)
            pool = in_il[area] if scheme == "tr.il" else [p for ilce in ilces for p in in_ilce[ilce]]
            index = defaultdict(list)
            for place, key in pool:
                index[key].append(place)
            seen = Counter(key for _, key in members)
            for unit, key in members:
                if unit in links or not key or seen[key] != 1:
                    continue
                old_ilce, old_bucak = unit_ilce[unit]
                plate = plate_of[unit]
                rows = []
                for row in renames[plate, key]:
                    row_ilce = fold(row["ilce"])
                    short = sorted((row_ilce, fold(old_ilce)), key=len)
                    same = short[0] == short[1] or (len(short[0]) > 3 and short[1].endswith(short[0]))
                    moved = scheme != "tr.il" and any(
                        name == row_ilce and ilce in ilces for il in plate_ils[plate] for name, ilce in ilce_names[il]
                    )
                    if same or moved:
                        rows.append(row)
                if len({fold(row["bucak"]) for row in rows}) > 1:
                    rows = [row for row in rows if fold(row["bucak"]) == fold(old_bucak)]
                hits = {
                    index[k][0]: row["new_name"]
                    for row in rows
                    for k in readings(row["new_name"])
                    if len(index[k]) == 1
                }
                if len(hits) == 1 and (place := next(iter(hits))) not in taken:
                    links[unit] = (place, "renamed")
                    renamed_to[unit] = hits[place]
        taken = {place for place, _ in links.values()}
        for plate, members in plates.items():
            index = defaultdict(list)
            for il in plate_ils[plate]:
                for place, key in in_il[il]:
                    index[key].append(place)
            seen = Counter(key for _, key in members)
            for unit, key in members:
                if unit in links or not key or seen[key] != 1:
                    continue
                rows = renames[plate, key]
                if len({fold(row["ilce"]) for row in rows}) != 1:
                    continue
                hits = {
                    index[k][0]: row["new_name"]
                    for row in rows
                    for k in readings(row["new_name"])
                    if len(index[k]) == 1
                }
                if len(hits) == 1 and (place := next(iter(hits))) not in taken:
                    links[unit] = (place, "renamed in province")
                    renamed_to[unit] = hits[place]
        twice = {place for place, count in Counter(place for place, _ in links.values()).items() if count > 1}
        links = {unit: link for unit, link in links.items() if link[0] not in twice}
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                [{"unit_id": str(unit), "area_id": str(place)} for unit, (place, _) in links.items()],
                schema={"unit_id": pl.String, "area_id": pl.String},
            ).with_columns(valid_from=pl.lit(None, pl.Date), valid_to=pl.lit(None, pl.Date), approximate=pl.lit(True)),
        )
        anchors = pl.read_csv(ANCHORS, separator="\t", infer_schema=False).fill_null("")
        anchors = anchors.filter((pl.col("area_2024") != "") & (pl.col("kaza") != "differs"))
        current = defaultdict(set)
        for area, text in conn.execute(
            "select entity_id, text from name where entity_id in (select id from area where scheme_code = 'tr.mahalle')"
        ).fetchall():
            current[area].add(fold(text))
        book = cs.source("İçişleri Bakanlığı", "Köylerimiz (1933)")
        cs.add(
            "source",
            id=book,
            kind="official_body",
            publisher="İçişleri Bakanlığı",
            title="Köylerimiz (1933)",
            url=KOYLERIMIZ_URL,
            license="Official public record, no license stated",
            notes="Village register of the republic's vilayets and kazas; the 1933 name is the printed one.",
        )
        seen, names = set(), []
        for row in anchors.sort("page", "column", "y").iter_rows(named=True):
            scheme, value = row["area_2024"].split(":", 1)
            area = cs.key(scheme, value)
            text = title(row["koy_1933"])
            key = fold(text)
            if not key or (area, key) in seen or key in current[area]:
                continue
            seen.add((area, key))
            names.append({"entity_id": str(area), "text": text, "source_id": str(book)})
        cs.frame(
            "name",
            pl.DataFrame(
                names, schema={"entity_id": pl.String, "text": pl.String, "source_id": pl.String}
            ).with_columns(
                lang=pl.lit("tr"),
                kind=pl.lit("historical"),
                valid_from=pl.lit("1933-01-01").str.to_date(),
                valid_from_precision=pl.lit("year"),
                is_preferred=pl.lit(False),
            ),
        )
        print(f"{len(names)} 1933 names on linked 2024 places")
        report = pl.DataFrame(
            [
                {
                    "plate": code.split(":")[1],
                    "code": code,
                    "name": text,
                    "linked": unit in links,
                    "pass": links[unit][1] if unit in links else None,
                    "renamed_to": renamed_to.get(unit) or misread.get(unit),
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
            f"by a name unique in the province, {sum(1 for _, how in links.values() if how.startswith('renamed'))} by a 1968 "
            f"rename, {sum(1 for _, how in links.values() if how.endswith('misread'))} by an OCR m or g reading; {len(twice)} places claimed twice left out"
        )
        return cs.write()
