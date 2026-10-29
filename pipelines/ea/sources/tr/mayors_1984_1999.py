import csv
import json
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.mayors_2004_2009 import LICENSE, LICENSE_URL, NUMBER, REF, cell, fold
from ea.sources.tr.ysk import INVALID, REGISTERED, VALID, VOTES, upper
from ea.sources.tr.ysk_local import DAYS, party
from ea.sources.tr.ysk_mayors import API, page
from ea.store import connect

SOURCE = "tr.mayors_1984_1999"
VERSION = "2"
YEARS = (1968, 1977, 1984, 1989, 1994, 1999)
UNPUBLISHED = Path(__file__).with_name("mayors_1984_1999_unpublished.tsv")
TITLE = re.compile(r"^(.+?)'(?:d[ae]|t[ae]) (\d{4}) Türkiye yerel seçimleri$")
METHOD = (
    "YSK's printed results for this election give mayors only by province. These counts are Turkish Wikipedia's "
    "per-province article, which cites the State Institute of Statistics' book and the day's newspapers. Each "
    "candidate's party and name are as the article gives them; where the article gives only the winner (most "
    "beldes), only the winner's votes are recorded."
)
PROVINCES = {"afyon": "afyonkarahisar", "icel": "mersin", "urfa": "sanliurfa"}
EXTRA = {"SODEP": "sosyal-demokrasi-partisi"}
Row = tuple[str, str, str, int]


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


def number(text: str) -> int | None:
    return int(text.replace(".", "")) if NUMBER.match(text) else None


def read(years: tuple[int, ...] = YEARS) -> tuple[dict, dict, list[str]]:
    tables: dict[tuple[int, str, str, str], tuple[str, list[Row], dict[str, int]]] = {}
    beldes: dict[tuple[int, str, str, str], list[tuple[str, str, str, int]]] = defaultdict(list)
    pages: list[str] = []
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
                    for chunk in re.split(r"\n\|-", part):
                        cells = [
                            cell(line) for line in chunk.split("\n") if line.startswith(("|", "!")) and line[:2] != "|}"
                        ]
                        if len(cells) >= 6 and cells[0] == "" and number(cells[4]) is not None:
                            rows.append((cells[1], cells[2], cells[3], number(cells[4])))
                        elif len(cells) == 4 and cells[0] == "" and rows and number(cells[3]) is None:
                            last = rows[-1]
                            rows[-1] = (last[0], last[1], f"{last[2]}; {cells[3]}", last[3])
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
    return tables, beldes, pages


def emit() -> Path:
    tables, beldes, pages = read()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayoral elections of 1968, 1977, 1984, 1989, 1994, and 1999 by municipality, from Turkish Wikipedia's per-province tables",
            polity="TR",
        )
        title = "Turkish Wikipedia: per-province 1968, 1977, 1984, 1989, 1994, and 1999 Türkiye yerel seçimleri articles"
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
        areas = dict(
            conn.execute(
                """select distinct on (office_id) office_id, district_area_id from contest
                   where district_area_id is not null and office_id is not null order by office_id, id"""
            ).fetchall()
        )
        units: dict[UUID, UUID] = dict(
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
        namesakes: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
        for office, person, text in conn.execute(
            """select c.office_id, ca.person_id, n.text from candidacy ca
               join contest_option co on co.id = ca.option_id join contest c on c.id = co.contest_id
               join name n on n.entity_id = ca.person_id
               join external_id x on x.entity_id = c.id and x.scheme = 'tr.contest'
               where c.kind = 'office'"""
        ).fetchall():
            namesakes[(office, fold(text))].add(person)

        abbreviations: dict[tuple[int, str], str] = {}
        for (year, _, _, _), (_, rows, _) in tables.items():
            for abbreviation, party_text, _, _ in rows:
                abbreviations[(year, fold(party_text))] = abbreviation
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
            races.append((year, keys, entries[0][0], name, [("", entries[0][1], entries[0][2], entries[0][3])], {}))

        polities = [cs.key("tr.polity", value) for value in existing]
        paths: dict[UUID, str] = {}
        english: dict[UUID, str] = {}
        for entity, text in conn.execute(
            "select entity_id, path from slug where entity_id = any(%s) and is_current order by length(path) desc",
            [polities],
        ).fetchall():
            paths[entity] = text
        for entity, text in conn.execute(
            "select entity_id, text from name where entity_id = any(%s) and lang = 'en' and kind = 'official'",
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

        skipped: list[tuple[str, str, str]] = []
        made: set[str] = set()
        results: list[dict[str, str | int | float | None]] = []
        complete = 0
        for year, keys, page_title, name, rows, totals in sorted(races, key=lambda race: (race[0], race[1][0])):
            day = DAYS[year]
            label = f"{year} {keys[0]} {name}"
            key = next((candidate for candidate in keys if candidate in existing), None)
            if key is None:
                skipped.append((str(year), label, "no municipality"))
                continue
            contest_key = f"{day}:{key}-mayor"
            if contest_key in made:
                skipped.append((str(year), label, "second table for the same municipality"))
                continue
            office = cs.key("tr.office", f"{key}-mayor")
            polity = cs.key("tr.polity", key)
            if contest_key in held and held[contest_key] not in own:
                skipped.append((str(year), label, "contest already held"))
                continue
            area = areas.get(office)
            if area is None or polity not in paths:
                skipped.append((str(year), label, "no area"))
                continue
            owners: list[str | None] = []
            unknown = None
            for abbreviation, party_text, _, _ in rows:
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
            made.add(contest_key)
            contest = cs.key("tr.contest", contest_key)
            path = paths[polity]
            place = english.get(polity, name)
            election = cs.key("tr.election", contest_key)
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
            unit = units.get(area)
            if unit is None:
                scheme = "tr.belde" if key.count("-") >= 2 else "tr.ilce"
                unit = cs.key(f"{SOURCE}.unit", key)
                units[area] = unit
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
            leader = max(rows, key=lambda row: row[3])
            tied = sum(1 for row in rows if row[3] == leader[3]) > 1
            for (_, party_text, candidates, votes), owner in zip(rows, owners, strict=True):
                names = [word for word in candidates.split("; ") if fold(word) not in ("", "bilinmiyor")]
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
                    reuse = namesakes.get((office, fold(person_name)), set())
                    if len(reuse) == 1:
                        person = next(iter(reuse))
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{key}:{slug(person_name)}")
                        namesakes[(office, fold(person_name))] = {person}
                        if person in cs.minted:
                            cs.add("person", id=person)
                            cs.add(
                                "name",
                                entity_id=person,
                                text=person_name,
                                lang="tr",
                                kind="official",
                                is_preferred=True,
                            )
                    candidacy = cs.key("tr.candidacy", f"{option_key}:{slug(person_name)}")
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
                    if votes == leader[3] and not tied and len(names) == 1:
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
        print(f"{len(made)} contests ({complete} complete); {len(skipped)} unpublished")
        return cs.write()
