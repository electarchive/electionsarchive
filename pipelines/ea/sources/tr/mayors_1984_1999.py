import csv
import difflib
import json
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.tr.mayors_2004_2009 import LICENSE, LICENSE_URL, NUMBER, REF, cell, fold
from ea.sources.tr.ysk import INVALID, REGISTERED, VALID, VOTES, upper
from ea.sources.tr.ysk import PROVINCES as PLATES
from ea.sources.tr.ysk_local import DAYS, own_area, own_areas, party
from ea.sources.tr.ysk_mayors import API, page
from ea.store import connect

SOURCE = "tr.mayors_1984_1999"
VERSION = "14"
YEARS = (1963, 1968, 1973, 1977, 1984, 1989, 1994, 1999)
UNPUBLISHED = Path(__file__).with_name("mayors_1984_1999_unpublished.tsv")
UNMERGED = Path(__file__).with_name("mayors_1984_1999_unmerged.tsv")
TITLE = re.compile(r"^(.+?)'(?:d[ae]|t[ae]) (\d{4}) Türkiye yerel seçimleri$")
METHOD = (
    "YSK's printed results for this election give mayors only by province. These counts are Turkish Wikipedia's "
    "per-province article, which cites the State Institute of Statistics' book and the day's newspapers. Each "
    "candidate's party and name are as the article gives them; where the article gives only the winner (most "
    "beldes), only the winner's votes are recorded."
)
PROVINCES = {"afyon": "afyonkarahisar", "icel": "mersin", "urfa": "sanliurfa"}
EXTRA = {"SODEP": "sosyal-demokrasi-partisi", "MSP": "milli-selamet-partisi"}
SUCCESSORS = {
    "adana": ("osmaniye",),
    "ankara": ("kirikkale",),
    "bolu": ("duzce",),
    "bursa": ("yalova",),
    "cankiri": ("karabuk",),
    "gaziantep": ("kilis",),
    "gumushane": ("bayburt",),
    "hakkari": ("sirnak",),
    "istanbul": ("yalova",),
    "kars": ("ardahan", "igdir"),
    "kocaeli": ("yalova",),
    "konya": ("aksaray", "karaman"),
    "mardin": ("batman", "sirnak"),
    "nigde": ("aksaray",),
    "siirt": ("batman", "sirnak"),
    "zonguldak": ("bartin", "karabuk"),
}
APART = {
    "balikesir-balya:mahmut-sari",
    "balikesir-susurluk:yusuf-akin",
    "burdur-bucak-kocaaliler:recep-ozcan",
    "burdur-bucak:mehmet-topay",
    "edirne-meric:mustafa-aksoy",
    "gaziantep-sehitkamil-aril:ahmet-pehlivan",
}
TOWNS = {
    "adiyaman-merkez-komurkoy": "tr/adiyaman/merkez/komur",
    "afyonkarahisar-sandikli-ahirhisar": "tr/afyonkarahisar-hocalar-ahirhisar",
    "afyonkarahisar-sincanli-sinanpasa-guneykoy": "tr/afyonkarahisar/sinanpasa/guney",
    "ankara-camlidere-karagedik": "tr/ankara/golbasi/karagedik",
    "ankara-camlidere-kutludugun": "tr/ankara/mamak/kutludugun",
    "canakkale-yenice-kalkin": "tr/canakkale/yenice/kalkim",
    "gumushane-aydintepe-arpali": "tr/bayburt/merkez/arpali",
    "isparta-gelendost-yakakoy": "tr/isparta/gelendost/yaka",
    "izmir-bergama-ayaz": "tr/izmir/bergama/ayaskent",
    "izmir-bergama-ayazkent": "tr/izmir/bergama/ayaskent",
    "izmir-kinik-yaya": "tr/izmir/kinik/yayakent",
    "izmir-kinik-yayakoy": "tr/izmir/kinik/yayakent",
    "kars-hanak-ortakoy": "tr/kars/hanak/ortakent",
    "kayseri-merkez-anarat": "tr/kayseri/kocasinan/amarat",
    "kayseri-sarioglan-ciftlikkoy": "tr/kayseri/sarioglan/ciftlik",
    "kirsehir-kaman-savcili": "tr/kirsehir/kaman/savcilibuyukoba",
    "kirsehir-kaman-savcilioba": "tr/kirsehir/kaman/savcilibuyukoba",
    "konya-beysehir-cavus": "tr/konya/huyuk/cavus",
    "kutahya-simav-demircikoy": "tr/kutahya/simav/demirci",
    "malatya-akcadag-yukarikozluca": "tr/malatya/akcadag/kozluca",
    "mugla-merkez-bayirkoy": "tr/mugla/merkez/bayir",
    "nigde-aksaray-toprakkaya": "tr/aksaray/merkez/topakkaya",
    "sivas-sarkisla-cakirseyh": "tr/sivas/sarkisla/cayirseyhi",
    "trabzon-akcaabat-cayirbasi": "tr/trabzon/duzkoy/cayirbagi",
}
Row = tuple[str, str, str, int, bool]
SHADED = re.compile(r'\|\s*style="?background')
ALSO = re.compile(r"^(.+ )(\S+) \((\S+)\)$")


def search(year: int) -> str:
    return (
        f"{API}?action=query&list=search&srsearch={quote(f'intitle:"{year} Türkiye yerel seçimleri"')}"
        "&srlimit=200&srnamespace=0&format=json"
    )


def fetch(years: tuple[int, ...] = YEARS) -> None:
    for year in years:
        raw.fetch(SOURCE, search(year), suffix=".json")
        for row in json.loads(raw.cached(SOURCE, search(year)).path.read_bytes())["query"]["search"]:
            for attempt in range(6):
                try:
                    raw.fetch(SOURCE, page(row["title"]), suffix=".json")
                    break
                except Exception:
                    if attempt == 5:
                        raise
                    time.sleep(20)
            time.sleep(1)


def fits(path: str, province: str, central: str) -> bool:
    district = path.split("/")[2]
    return (district == "merkez" and path.split("/")[1] == province) or bool(
        set(district.split("-")) & set(central.split("-"))
    )


def number(text: str) -> int | None:
    return int(text.replace(".", "")) if NUMBER.match(text) else None


def read(years: tuple[int, ...] = YEARS) -> tuple[dict, dict, list[str], list[tuple[int, str, str]]]:
    tables: dict[tuple[int, str, str, str], tuple[str, list[Row], dict[str, int]]] = {}
    beldes: dict[tuple[int, str, str, str], list[tuple[str, str, str, int]]] = defaultdict(list)
    pages: list[str] = []
    reruns: list[tuple[int, str, str]] = []
    for year in years:
        for hit in json.loads(raw.cached(SOURCE, search(year)).path.read_bytes())["query"]["search"]:
            match = TITLE.match(hit["title"])
            if match is None:
                continue
            pages.append(hit["title"])
            province = slug(match.group(1))
            text = REF.sub(
                "", json.loads(raw.cached(SOURCE, page(hit["title"])).path.read_bytes())["parse"]["wikitext"]
            )
            section = ""
            district = ""
            for part in re.split(r"\n(?==+ ?[^=\n]+ ?=+\s*\n)", text):
                head = re.match(r"(=+) ?(.+?) ?=+\s*(?:\n|$)", part)
                level = len(head.group(1)) if head else 0
                heading = cell("|" + head.group(2)) if head else ""
                if level == 2:
                    section = fold(heading)
                    district = ""
                elif level == 3:
                    district = heading
                if "{{Seçim tablosu" in part:
                    rows: list[Row] = []
                    totals: dict[str, int] = {}
                    first, *later = part.split("{{Seçim tablosu")[1:]
                    reruns.extend((year, province, district or province) for _ in later)
                    for chunk in re.split(r"\n\|-", first):
                        lines = [line for line in chunk.split("\n") if line.startswith(("|", "!")) and line[:2] != "|}"]
                        cells = [cell(line) for line in lines]
                        won = any(SHADED.match(line) for line in lines[1:4])
                        if len(cells) >= 6 and cells[0] == "" and number(cells[4]) is not None:
                            rows.append((cells[1], cells[2], cells[3], number(cells[4]), won))
                        elif len(cells) == 4 and cells[0] == "" and rows and number(cells[3]) is None:
                            last = rows[-1]
                            names = f"{cells[3]}; {last[2]}" if won and not last[4] else f"{last[2]}; {cells[3]}"
                            rows[-1] = (last[0], last[1], names, last[3], last[4] or won)
                        elif (
                            cells
                            and cells[0].startswith("Toplam")
                            and len(cells) >= 2
                            and number(cells[-1]) is not None
                        ):
                            totals["valid"] = number(cells[-1])
                        elif cells and fold(cells[0]).startswith("gecersiz") and number(cells[-1]) is not None:
                            totals["invalid"] = number(cells[-1])
                        elif (
                            len(cells) >= 2
                            and all(number(cell_) is not None for cell_ in cells[:2])
                            and "voters" not in totals
                        ):
                            totals["voters"] = number(cells[0])
                    if section.endswith("buyuksehirbelediyesi") or "buyuksehir" in section:
                        kind = "bb"
                    elif section.endswith("merkez"):
                        kind = "merkez"
                    else:
                        kind = "ilce"
                    if rows:
                        tables[(year, province, kind, district if kind == "ilce" else province)] = (
                            hit["title"],
                            rows,
                            totals,
                        )
                elif level == 4 and heading.endswith("beldeleri"):
                    for chunk in re.split(r"\n\|-", part):
                        cells = [cell(line) for line in chunk.split("\n") if line.startswith("|") and line[:2] != "|}"]
                        if len(cells) >= 4 and cells[0] and number(cells[3]) is not None:
                            beldes[
                                (year, province, section.endswith("merkez") and "merkez" or district, cells[0])
                            ].append((hit["title"], cells[1], cells[2], number(cells[3])))
    return tables, beldes, pages, reruns


def also(text: str) -> tuple[str, str | None]:
    match = ALSO.match(text)
    return (match[1] + match[2], match[1] + match[3]) if match else (text, None)


def named(cs: Changeset, person: UUID, text: str) -> None:
    shown, other = also(text)
    cs.add("name", entity_id=person, text=shown, lang="tr", kind="official", is_preferred=True)
    if other is not None:
        cs.add("name", entity_id=person, text=other, lang="tr", kind="official", is_preferred=False)


def rename(cs: Changeset, pipeline: str, wanted: dict[UUID, list[str]]) -> None:
    held: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name_id, entity, text in cs.conn.execute(
        """select n.id::text, n.entity_id::text, n.text from name n join changeset ch on ch.id = n.changeset_id
           where ch.pipeline = %s and n.entity_id = any(%s::uuid[])
             and exists (select from name p join changeset pc on pc.id = p.changeset_id
                         where p.entity_id = n.entity_id and p.is_preferred and pc.pipeline = %s)""",
        [pipeline, [person for person in wanted if person not in cs.minted], pipeline],
    ).fetchall():
        held[entity].append((name_id, text))
    present = set(
        cs.conn.execute(
            "select entity_id, text from name where entity_id = any(%s::uuid[])",
            [[person for person in wanted if person not in cs.minted]],
        ).fetchall()
    )
    for person, spellings in wanted.items():
        text, first = also(spellings[0])
        others = {form for written in spellings for form in also(written) if form is not None} - {text}
        names = held.get(str(person), [])
        olds = {old for _, old in names}
        for other in sorted(others):
            if (person, other) not in present and not (person in cs.minted and other == first):
                cs.add("name", entity_id=person, text=other, lang="tr", kind="official", is_preferred=False)
        if all(old == text or old in others or (cell(f"|{old}") == old and ALSO.match(old) is None) for old in olds):
            continue
        if text not in olds:
            cs.add("name", entity_id=person, text=text, lang="tr", kind="official", is_preferred=True)
        for name_id, old in names:
            if old != text and old not in others:
                cs.add(
                    "name_drop",
                    name_id=name_id,
                    entity_id=person,
                    text=old,
                    reason="Read from a misparsed Wikipedia table cell",
                )


def emit() -> Path:
    tables, beldes, pages, reruns = read()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayoral elections of 1963, 1968, 1973, 1977, 1984, 1989, 1994, and 1999 by municipality, from Turkish Wikipedia's per-province tables",
            polity="TR",
        )
        title = "Turkish Wikipedia: per-province 1963, 1968, 1973, 1977, 1984, 1989, 1994, and 1999 Türkiye yerel seçimleri articles"
        wiki = cs.source("Wikipedia", title)
        if wiki in cs.minted:
            cs.add(
                "source",
                id=wiki,
                kind="community",
                publisher="Wikipedia",
                title=title,
                url="https://tr.wikipedia.org/wiki/1994_Türkiye_yerel_seçimleri",
                license=LICENSE,
                license_url=LICENSE_URL,
            )
        for page_title in pages:
            cs.artifact(raw.cached(SOURCE, page(page_title)), wiki)

        mayoral = cs.key("tr.electoral_system", "mayor")
        existing = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'tr.polity'").fetchall()
        }
        areas: dict[UUID, list[tuple[bool, date, UUID]]] = defaultdict(list)
        for office_id, held_on, area_id, scheme in conn.execute(
            """select c.office_id, e.date_from, c.district_area_id, a.scheme_code from contest c
               join election e on e.id = c.election_id join area a on a.id = c.district_area_id
               where c.office_id is not null and e.date_from is not null and a.scheme_code like 'tr.%'"""
        ).fetchall():
            areas[office_id].append((scheme in ("tr.il", "tr.buyuksehir"), held_on, area_id))
        spans = own_areas(conn)
        units: dict[UUID | str, UUID] = dict(
            conn.execute(
                """select area_id, id from reporting_unit
                   where scheme_code in ('tr.ilce', 'tr.belde') and kind = 'aggregate' and area_id is not null
                     and area_relation in ('within', 'equals')"""
            ).fetchall()
        )
        party_names = dict(
            conn.execute(
                """select e.value, (select text from name n where n.entity_id = e.entity_id and n.kind = 'official'
                                    order by n.valid_from desc nulls last limit 1)
                   from external_id e where e.scheme = 'tr.party'"""
            ).fetchall()
        )
        abbreviations: dict[tuple[int, str], str] = {}
        for (year, _, _, _), (_, rows, _) in tables.items():
            for abbreviation, party_text, _, _, _ in rows:
                abbreviations[(year, fold(party_text))] = abbreviation
        mine = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join changeset c on c.id = x.changeset_id
                   where x.scheme = 'tr.polity' and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        }
        held_beldes: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        held_ilces: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        for value, path in conn.execute(
            """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
               where x.scheme = 'tr.polity' and s.path ~ '^tr/[^/]+/[^/]+(/[^/]+)?$'"""
        ).fetchall():
            parts = path.split("/")
            (held_beldes if len(parts) == 4 else held_ilces)[(parts[1], parts[-1])][path] = value
        latest = dict(conn.execute("select polity_id, max(date_from) from election group by polity_id").fetchall())
        at = dict(
            conn.execute(
                """select s.path, x.value from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'tr.polity'"""
            ).fetchall()
        )
        matches: dict[str, tuple[str, dict[str, str]]] = {}
        races: list[tuple[int, list[str], str, str, list[Row], dict[str, int]]] = []
        for (year, province, kind, name), (page_title, rows, totals) in tables.items():
            province = PROVINCES.get(province, province)
            if kind in ("bb", "merkez"):
                keys = [province]
            else:
                base = re.sub(r"\s*\(.*\)$", "", name)
                keys = [f"{province}-{slug(word)}" for word in (base, *re.findall(r"\((.*?)\)", name))]
            races.append((year, keys, page_title, name, rows, totals))
        for (year, province, district, name), entries in beldes.items():
            province = PROVINCES.get(province, province)
            central = slug(district)
            keys = [f"{province}-{central}-{slug(name)}", f"{province}-{slug(name)}", f"{province}-merkez-{slug(name)}"]
            if keys[0] in TOWNS:
                matches[keys[0]] = (name, {TOWNS[keys[0]]: at[TOWNS[keys[0]]]})
            elif keys[0] in mine:
                family = (province, *SUCCESSORS.get(province, ()))
                words = [slug(part) for part in re.split(r"[()/,-]", name) if slug(part)]
                found = {
                    path: value for place in family for path, value in held_beldes.get((place, slug(name)), {}).items()
                }
                for held in (held_beldes, held_ilces):
                    if not found:
                        found = {
                            path: value
                            for place in family
                            for word in words
                            for path, value in held.get((place, word), {}).items()
                        }
                if not found:
                    scored = [
                        (difflib.SequenceMatcher(None, word, belde).ratio(), path, value)
                        for (place, belde), held in held_beldes.items()
                        if place in family
                        for path, value in held.items()
                        if fits(path, province, central)
                        for word in words
                        if len(word) >= 6
                    ]
                    best = max((ratio for ratio, _, _ in scored), default=0)
                    found = {path: value for ratio, path, value in scored if ratio == best >= 0.88}
                pool = found
                if len(pool) > 1 and all(path.count("/") == 3 for path in pool):
                    pool = {path: value for path, value in pool.items() if fits(path, province, central)}
                    pool = {path: value for path, value in pool.items() if path.split("/")[2] == central} or pool
                if len(pool) > 1:
                    held_on = {path: latest.get(cs.key("tr.polity", value)) or date.min for path, value in pool.items()}
                    pool = {path: value for path, value in pool.items() if held_on[path] == max(held_on.values())}
                matches[keys[0]] = (name, pool if len(pool) == 1 else found)
            races.append(
                (
                    year,
                    keys,
                    entries[0][0],
                    name,
                    [("", entries[0][1], entries[0][2], entries[0][3], "; " not in entries[0][2])],
                    {},
                )
            )

        for value, (_, found) in matches.items():
            if len(found) == 1:
                cs.merge(cs.key("tr.polity", value), cs.key("tr.polity", next(iter(found.values()))))
        mayors: dict[UUID, dict[UUID, bool]] = defaultdict(dict)
        for polity_id, office_id, ours in conn.execute(
            """select o.polity_id, o.id, c.pipeline = %s from office o join changeset c on c.id = o.changeset_id
               join entity e on e.id = o.id and e.retired_by is null
               where o.kind = 'mayor' and not exists (select from entity_merge m where m.entity_id = o.id)
                 and exists (select from external_id x where x.entity_id = o.id and x.scheme = 'tr.office')""",
            [SOURCE],
        ).fetchall():
            mayors[cs.survivors.get(polity_id, polity_id)][office_id] = ours
        for offices in mayors.values():
            theirs = [office for office, ours in offices.items() if not ours]
            into = theirs[0] if len(theirs) == 1 else None if theirs else min(offices)
            for office, ours in offices.items():
                if ours and into is not None and office != into:
                    cs.merge(office, into)
        careers: dict[tuple[UUID, str], dict[UUID, tuple[bool, set[int], set[UUID]]]] = defaultdict(dict)
        posts: dict[UUID, set[UUID]] = defaultdict(set)
        apart = {cs.key(f"{SOURCE}.person", f"{value}:apart") for value in APART}
        for office_id, person_id, text, year, contest_id, ours in conn.execute(
            """with held as (
                 select c.office_id, ca.person_id, e.date_from on_day, c.id contest_id from candidacy ca
                 join contest_option co on co.id = ca.option_id join contest c on c.id = co.contest_id
                 join election e on e.id = c.election_id
                 union all
                 select office_id, person_id, from_date, null from tenure)
               select h.office_id, h.person_id, n.text, extract(year from h.on_day)::int, h.contest_id, c.pipeline = %s
               from held h join office o on o.id = h.office_id and o.kind = 'mayor'
               join entity e on e.id = h.person_id and e.retired_by is null
               join person p on p.id = h.person_id join changeset c on c.id = p.changeset_id
               join name n on n.entity_id = p.id and n.is_preferred
               where h.on_day is not null
                 and exists (select from external_id x where x.entity_id = o.id and x.scheme = 'tr.office')
                 and not exists (select from entity_merge m where m.entity_id = p.id)""",
            [SOURCE],
        ).fetchall():
            career = careers[(cs.survivors.get(office_id, office_id), fold(text))].setdefault(
                person_id, (ours, set(), set())
            )
            career[1].add(year)
            posts[person_id].add(cs.survivors.get(office_id, office_id))
            if contest_id is not None:
                career[2].add(contest_id)
        for (office, _), people in careers.items():
            theirs = [person for person, (ours, _, _) in people.items() if not ours]
            if (
                len(people) < 2
                or len(theirs) > 1
                or (people.keys() & apart and any(posts[person] - {office} for person in people))
            ):
                continue
            into = theirs[0] if theirs else min(people, key=lambda person: min(people[person][1]))
            pending = [person for person in people if person != into]
            while pending:
                near = [
                    person
                    for person in pending
                    if not people[person][2] & people[into][2]
                    and min(abs(a - b) for a in people[person][1] for b in people[into][1]) <= 7
                ]
                if not near:
                    break
                for person in near:
                    cs.merge(person, into)
                    people[into][1].update(people[person][1])
                    people[into][2].update(people[person][2])
                    pending.remove(person)
        holders: dict[UUID, UUID] = dict(
            conn.execute(
                """select ca.id, ca.person_id from candidacy ca join changeset ch on ch.id = ca.changeset_id
                   where ch.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        namesakes: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
        for office, person, text in conn.execute(
            """select c.office_id, ca.person_id, n.text from candidacy ca
               join contest_option co on co.id = ca.option_id join contest c on c.id = co.contest_id
               join name n on n.entity_id = ca.person_id
               join external_id x on x.entity_id = c.id and x.scheme = 'tr.contest'
               where c.kind = 'office'"""
        ).fetchall():
            namesakes[(cs.survivors.get(office, office), fold(text))].add(cs.survivors.get(person, person))
        own_polities = {
            polity
            for (polity,) in conn.execute(
                "select p.id from polity p join changeset c on c.id = p.changeset_id where c.pipeline = %s", [SOURCE]
            ).fetchall()
        }
        with UNMERGED.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(("place", "name", "candidates"))
            writer.writerows(
                sorted(
                    (value, name, " ".join(sorted(found)))
                    for value, (name, found) in matches.items()
                    if len(found) != 1 and cs.key("tr.polity", value) in own_polities
                )
            )
        turkey = cs.polity("TR")
        plates = {slug(text): f"{plate:02d}" for plate, text in enumerate(PLATES, 1)}
        provinces = {cs.area("tr.il", plate) for plate in plates.values()}
        polities = [cs.key("tr.polity", value) for value in existing]
        paths: dict[UUID, str] = {}
        english: dict[UUID, str] = {}
        for entity, text in conn.execute(
            "select entity_id, path from slug where entity_id = any(%s) and is_current order by length(path) desc",
            [polities],
        ).fetchall():
            paths[entity] = text
        for entity, text in conn.execute(
            "select entity_id, text from name where entity_id = any(%s) and lang = 'en' and kind = 'official' and is_preferred",
            [polities],
        ).fetchall():
            english[entity] = re.sub(r" Municipality$", "", text)
        held = dict(conn.execute("select value, entity_id from external_id where scheme = 'tr.contest'").fetchall())
        own = {
            contest
            for (contest,) in conn.execute(
                "select id from contest where changeset_id in (select id from changeset where pipeline = %s)", [SOURCE]
            ).fetchall()
        }

        taken: dict[str, UUID] = dict(
            conn.execute(
                "select path, entity_id from slug where path like any(%s)",
                [[f"%/{day}-local" for day in DAYS.values()]],
            ).fetchall()
        )
        prior = dict(conn.execute("select value, entity_id from external_id where scheme = 'tr.election'").fetchall())
        elsewhere = {
            (cs.survivors.get(polity, polity), day): election
            for polity, day, election in conn.execute(
                """select e.polity_id, e.date_from, e.id from election e
                   join contest c on c.election_id = e.id join office o on o.id = c.office_id and o.kind = 'mayor'
                   join changeset ch on ch.id = e.changeset_id
                   where ch.pipeline <> %s and e.polity_id is not null""",
                [SOURCE],
            ).fetchall()
        }
        skipped: list[tuple[str, str, str]] = [
            (str(year), f"{year} {province} {district}", "by-election after the regular race")
            for year, province, district in reruns
        ]
        made: set[str] = set()
        placed: dict[tuple[UUID, date], UUID] = {}
        moved: dict[str, UUID] = {}
        doubled: dict[str, UUID] = {}
        results: list[dict[str, str | int | float | None]] = []
        wanted: dict[UUID, list[str]] = defaultdict(list)
        complete = 0
        for year, keys, page_title, name, rows, totals in sorted(races, key=lambda race: (race[0], race[1][0])):
            day = DAYS[year]
            label = f"{year} {keys[0]} {name}"
            key = next((candidate for candidate in keys if candidate in existing), None)
            minted_place = False
            if key is None:
                key = keys[0]
                minted_place = key.count("-") >= 2 and key.split("-")[0] in plates
                if not minted_place:
                    skipped.append((str(year), label, "no municipality"))
                    continue
            contest_key = f"{day}:{key}-mayor"
            if contest_key in made or (cs.key("tr.polity", key), day) in placed:
                if (cs.key("tr.polity", key), day) in placed:
                    doubled[contest_key] = placed[(cs.key("tr.polity", key), day)]
                skipped.append((str(year), label, "second table for the same municipality"))
                continue
            office = cs.key("tr.office", f"{key}-mayor")
            polity = cs.key("tr.polity", key)
            if minted_place and polity in cs.minted and polity not in paths:
                paths[polity] = f"tr/{key}"
                english[polity] = name
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                for text, lang, kind in (
                    (f"{name} Municipality", "en", "official"),
                    (f"{name} Belediyesi", "tr", "native"),
                ):
                    cs.add("name", entity_id=polity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")
                cs.add("polity_parent", polity_id=polity, parent_id=turkey)
                cs.add("slug", path=paths[polity], entity_id=polity)
                cs.add("office", id=office, polity_id=polity, kind="mayor")
                cs.add("name", entity_id=office, text=f"Mayor of {name}", lang="en", kind="official", is_preferred=True)
                cs.add("name", entity_id=office, text=f"{name} Belediye Başkanı", lang="tr", kind="native")
            if (contest_key in held and held[contest_key] not in own) or uuid5(
                NAMESPACE, f"external_id:tr.election\x1f{contest_key}"
            ) in cs.retired:
                skipped.append((str(year), label, "contest already held"))
                continue
            nearest = min(areas.get(office, []), key=lambda held: (held[0], abs((held[1] - day).days)), default=None)
            area = (
                own_area(spans, polity, day)
                or (None if nearest is None else nearest[2])
                or (cs.area("tr.il", plates[key.split("-")[0]]) if minted_place else None)
            )
            if area is None or polity not in paths:
                skipped.append((str(year), label, "no area"))
                continue
            owners: list[str | None] = []
            unknown = None
            for abbreviation, party_text, _, _, _ in rows:
                owner = None
                abbreviation = abbreviation or abbreviations.get((year, fold(party_text)), "")
                if abbreviation not in ("BĞMSZ", "BAĞIMSIZ") and not fold(party_text).startswith("bagimsiz"):
                    for candidate in (party_text, upper(party_text), abbreviation):
                        try:
                            owner = party(candidate, year)
                        except KeyError:
                            owner = EXTRA.get(abbreviation)
                        if owner is not None:
                            break
                    if owner is None:
                        unknown = party_text
                        break
                owners.append(owner)
            if unknown is not None:
                skipped.append((str(year), label, f"unknown party {unknown}"))
                continue
            if (polity, day) in elsewhere or taken.get(
                f"{paths[polity]}/{day}-local", prior.get(contest_key)
            ) != prior.get(contest_key):
                moved[contest_key] = elsewhere.get((polity, day)) or taken[f"{paths[polity]}/{day}-local"]
                skipped.append((str(year), label, "election already held"))
                continue
            election = cs.key("tr.election", contest_key)
            made.add(contest_key)
            contest = cs.key("tr.contest", contest_key)
            placed[(polity, day)] = contest
            path = paths[polity]
            place = english.get(polity, name)
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=cs.key("tr.election", f"{day}:local"),
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            cs.add(
                "name",
                entity_id=election,
                text=f"{year} {place} mayoral election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=mayoral,
                default_result_kind="counted",
            )
            cs.add("name", entity_id=contest, text=f"Mayor of {place}", lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"{path}/{day}-local/mayor", entity_id=contest)
            slot = key if area in provinces else area
            unit = units.get(slot)
            if unit is None:
                scheme = "tr.belde" if key.count("-") >= 2 else "tr.ilce"
                unit = cs.key(f"{SOURCE}.unit", key)
                units[slot] = unit
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=f"{year}:{key}",
                    area_relation="within",
                    area_id=area,
                    kind="aggregate",
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            is_complete = len(rows) > 1 and "valid" in totals
            complete += is_complete
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=wiki,
                is_default=True,
                completeness=1 if is_complete else 0,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            cs.add("citation", entity_id=result_set, source_id=wiki, locator={"page": page_title, "section": name})
            for measure, field in ((REGISTERED, "voters"), (VALID, "valid"), (INVALID, "invalid")):
                if field in totals:
                    results.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": None,
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "value": totals[field],
                            "flags": 0,
                        }
                    )
            used: set[str] = set()
            for (_, party_text, candidates, votes, won), owner in zip(rows, owners, strict=True):
                names = [
                    word for word in candidates.split("; ") if fold(word) not in ("", "bilinmiyor", "bilinmeyenadaylar")
                ]
                option_key = f"{contest_key}:{owner or slug(candidates)}"
                if option_key in used:
                    option_key = f"{option_key}:{slug(candidates)}"
                used.add(option_key)
                option = cs.key("tr.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if owner is not None or len(names) < 2 else "independents",
                    party_id=None if owner is None else cs.key("tr.party", owner),
                    ballot_label=candidates if owner is None else party_names[owner],
                )
                results.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": str(option),
                        "channel": 0,
                        "measure": VOTES,
                        "count_round": 0,
                        "value": votes,
                        "flags": 0,
                    }
                )
                for person_name in names:
                    value = f"{key}:{slug(person_name)}"
                    reuse = set() if value in APART else namesakes.get((office, fold(also(person_name)[0])), set())
                    candidacy = cs.key("tr.candidacy", f"{option_key}:{slug(person_name)}")
                    holder = holders.get(candidacy)
                    if len(reuse) == 1:
                        person = next(iter(reuse))
                    elif holder is not None and cs.survivors.get(holder, holder) in reuse:
                        person = cs.survivors.get(holder, holder)
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{value}:apart" if value in APART else value)
                        namesakes[(office, fold(also(person_name)[0]))] = {person}
                        if person in cs.minted:
                            cs.add("person", id=person)
                            named(cs, person, person_name)
                    wanted[person].append(person_name)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        member_of_party_id=None if owner is None else cs.key("tr.party", owner),
                        is_independent=owner is None,
                    )
                    if owner is not None:
                        cs.add(
                            "candidacy_nomination",
                            candidacy_id=candidacy,
                            party_id=cs.key("tr.party", owner),
                            kind="nominated",
                        )
                    if won and person_name == names[0]:
                        start, end = date(year, 4, 1), date(year + 5, 4, 1)
                        tenure = cs.key(f"{SOURCE}.tenure", contest_key)
                        cs.add(
                            "tenure",
                            id=tenure,
                            person_id=person,
                            office_id=office,
                            from_date=start,
                            from_precision="month",
                            to_date=end,
                            to_precision="month",
                            acquired_via="elected",
                            contest_id=contest,
                            ended_via="term_end",
                        )
                        if owner is not None:
                            cs.add(
                                "tenure_affiliation",
                                tenure_id=tenure,
                                kind="party",
                                party_id=cs.key("tr.party", owner),
                                valid_from=start,
                                valid_from_precision="month",
                                valid_to=end,
                                valid_to_precision="month",
                            )
                        cs.add(
                            "citation", entity_id=tenure, source_id=wiki, locator={"page": page_title, "section": name}
                        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                results,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        with UNPUBLISHED.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(("year", "race", "reason"))
            writer.writerows(sorted(skipped, key=lambda row: (row[2], row[0], row[1])))
        options = {str(row["id"]) for row in cs.rows.get("contest_option", [])}
        reason = "An option an earlier reading of the Wikipedia article gave this race, from a misread party or a later by-election's table."
        for option, candidacy in conn.execute(
            """select o.id::text, ca.id::text from contest_option o join changeset c on c.id = o.changeset_id
               join entity e on e.id = o.id and e.retired_by is null
               left join candidacy ca on ca.option_id = o.id
               where c.pipeline = %s and o.contest_id = any(%s::uuid[])""",
            [SOURCE, [str(row["id"]) for row in cs.rows.get("contest", [])]],
        ).fetchall():
            if option not in options:
                if candidacy is not None:
                    cs.add("entity_retirement", entity_id=candidacy, reason=reason)
                cs.add("entity_retirement", entity_id=option, reason=reason)
        candidacies = {str(row["id"]) for row in cs.rows.get("candidacy", [])}
        stale = [
            candidacy
            for (candidacy,) in conn.execute(
                """select ca.id::text from candidacy ca join changeset ch on ch.id = ca.changeset_id
                   join contest_option o on o.id = ca.option_id
                   where ch.pipeline = %s and o.id = any(%s::uuid[]) and ca.id <> all(%s::uuid[])""",
                [SOURCE, list(options), list(candidacies)],
            ).fetchall()
        ]
        for candidacy in stale:
            cs.add(
                "entity_retirement",
                entity_id=candidacy,
                reason="A candidacy read from a misparsed Wikipedia name cell, replaced by the candidates the cell names.",
            )
        terms = [str(row["id"]) for row in cs.rows.get("tenure", [])]
        for (tenure,) in conn.execute(
            """select t.id::text from tenure t join changeset ch on ch.id = t.changeset_id
               where ch.pipeline = %s and t.contest_id = any(%s::uuid[]) and t.id <> all(%s::uuid[])""",
            [SOURCE, [str(row["id"]) for row in cs.rows.get("contest", [])], terms],
        ).fetchall():
            cs.add(
                "entity_retirement",
                entity_id=tenure,
                reason="A term an earlier reading of the Wikipedia article put on this race, whose winner the article shades differently or names only in a later by-election.",
            )
        rename(cs, SOURCE, wanted)
        survivors = dict(
            conn.execute(
                """select c.election_id, c.id from contest c join office o on o.id = c.office_id and o.kind = 'mayor'
                   where c.election_id = any(%s::uuid[])""",
                [list(moved.values())],
            ).fetchall()
        )
        staged = {table: {str(row["id"]) for row in cs.rows.get(table, [])} for table in ("contest", "election")}
        reason = "A duplicate of a municipal race the archive already held, left behind when the archive merged the town into its belde."
        retiring: dict[str, UUID | None] = {}
        for contest_id, election_id, value in conn.execute(
            """select c.id::text, c.election_id::text, x.value from contest c join changeset ch on ch.id = c.changeset_id
               join external_id x on x.entity_id = c.id and x.scheme = 'tr.contest'
               where ch.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if contest_id not in staged["contest"]:
                retiring[contest_id] = doubled.get(value, survivors.get(moved.get(value)))
                cs.add("entity_retirement", entity_id=contest_id, reason=reason)
                if election_id not in staged["election"]:
                    cs.add("entity_retirement", entity_id=election_id, reason=reason)
        tenures = conn.execute("select * from tenure where contest_id = any(%s::uuid[])", [list(retiring)])
        columns = [column.name for column in tenures.description or ()]
        for values in tenures.fetchall():
            row = dict(zip(columns, values, strict=True))
            cs.add(
                "tenure",
                **{column: value for column, value in row.items() if column not in ("changeset_id", "recorded_from")}
                | {
                    "contest_id": retiring[str(row["contest_id"])],
                    "office_id": cs.survivors.get(row["office_id"], row["office_id"]),
                },
            )
        for (person,) in conn.execute(
            """select distinct cy.person_id::text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = cy.person_id and x.scheme = %s
               where o.contest_id = any(%s::uuid[])
                 and not exists (select from tenure t where t.person_id = cy.person_id)
                 and not exists (
                   select from candidacy other join contest_option oo on oo.id = other.option_id
                   where other.person_id = cy.person_id and oo.contest_id <> all(%s::uuid[]))""",
            [f"{SOURCE}.person", list(retiring), list(retiring)],
        ).fetchall():
            if UUID(person) not in cs.survivors:
                cs.add("entity_retirement", entity_id=person, reason=reason)
        retired = {str(row["entity_id"]) for row in cs.rows.get("entity_retirement", [])}
        referenced = {str(row["person_id"]) for table in ("candidacy", "tenure") for row in cs.rows.get(table, [])}
        for (person,) in conn.execute(
            """select p.id::text from person p join changeset ch on ch.id = p.changeset_id
               join entity e on e.id = p.id and e.retired_by is null
               where ch.pipeline = %s
                 and not exists (select from entity_merge m where m.entity_id = p.id or m.into_id = p.id)
                 and not exists (select from candidacy c where c.person_id = p.id and c.id <> all(%s::uuid[]))
                 and not exists (select from tenure t where t.person_id = p.id and t.id <> all(%s::uuid[]))""",
            [SOURCE, [*retired, *candidacies], [*retired, *terms]],
        ).fetchall():
            if person not in retired and person not in referenced and UUID(person) not in cs.survivors:
                cs.add(
                    "entity_retirement",
                    entity_id=person,
                    reason="A person read from the Wikipedia article whom the archive no longer links to any candidacy or term.",
                )
        used = {row["unit_id"] for row in results}
        for (unit,) in conn.execute(
            """select ru.id::text from reporting_unit ru join changeset ch on ch.id = ru.changeset_id
               where ch.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if unit not in used:
                cs.add(
                    "entity_retirement",
                    entity_id=unit,
                    reason="A municipal reporting unit the archive no longer uses, after merging the town into its belde.",
                )
        fresh = {
            (str(row["entity_id"]), row["lang"], row["kind"]): row["text"]
            for row in cs.rows.get("name", [])
            if row["is_preferred"]
        }
        dropping = {str(row["name_id"]) for row in cs.rows.get("name_drop", [])}
        for name_id, entity, lang, kind, text in conn.execute(
            """select n.id::text, n.entity_id::text, n.lang, n.kind, n.text from name n
               join changeset ch on ch.id = n.changeset_id where ch.pipeline = %s and n.is_preferred""",
            [SOURCE],
        ).fetchall():
            if name_id not in dropping and fresh.get((entity, lang, kind), text) != text:
                cs.add(
                    "name_drop",
                    name_id=name_id,
                    entity_id=entity,
                    text=text,
                    reason="Named with another or a misread spelling of the town instead of the municipality's preferred name.",
                )
        print(f"{len(made)} contests ({complete} complete); {len(skipped)} unpublished")
        return cs.write()
