import csv
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.tr.mayors_2004_2009 import LICENSE, LICENSE_URL, fold, read_tables
from ea.sources.tr.ysk_mayors import SOURCE as MAYORS
from ea.sources.tr.ysk_mayors import page
from ea.store import connect

SOURCE = "tr.mayors_2009_votes"
VERSION = "1"
DAY = date(2009, 3, 29)
VOTES = 1
UNPUBLISHED = Path(__file__).with_name("mayors_2009_votes_unpublished.tsv")
METHOD = (
    "YSK published the 2009 candidates by municipality but no vote counts below the province. The counts here are "
    "Turkish Wikipedia's per-province tables, which cite YSK. Each table was matched to its municipality by its "
    "candidates' names, and each row to a candidate by party and name; municipalities where a row matched no "
    "candidate are left out. Where the article gives only the winner (most beldes), only the winner's votes are "
    "recorded."
)
OPTIONS = """
select c.id, c.election_id, e.polity_id, c.district_area_id, x.value, co.id, co.party_id, ca.person_id,
  (select n.text from name n where n.entity_id = ca.person_id order by n.is_preferred desc limit 1)
from contest c
join election e on e.id = c.election_id and e.date_from = %s
join external_id x on x.entity_id = c.id and x.scheme = 'tr.contest'
join contest_option co on co.contest_id = c.id
left join candidacy ca on ca.option_id = co.id
where c.kind = 'office' and not exists (select 1 from result_set rs where rs.contest_id = c.id)
"""


def same(candidate: str, person: str) -> bool:
    return SequenceMatcher(None, fold(candidate), fold(person)).ratio() >= 0.85 or (
        2 <= len(person.split())
        and {fold(word) for word in person.split()} <= {fold(word) for word in candidate.split()}
    )


def emit() -> Path:
    tables, beldes, pages = read_tables([2009])
    tables_2009 = tables[2009]
    by_name: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, table in enumerate(tables_2009):
        for _, person_name, _ in table[3]:
            by_name[(table[2], fold(person_name))].append(index)

    with connect() as conn:
        party_ids: dict[str, set[UUID | None]] = defaultdict(set)
        for entity, text in conn.execute(
            "select n.entity_id, n.text from name n join party p on p.id = n.entity_id"
        ).fetchall():
            party_ids[fold(text)].add(entity)
        party_ids[fold("Bağımsız")].add(None)
        party_ids[fold("Bağımsızlar")].add(None)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's 2009 mayoral vote counts by municipality, from Turkish Wikipedia's per-province tables",
            polity="TR",
        )
        wiki = cs.source("Wikipedia", "Turkish Wikipedia: per-province 2009 Türkiye yerel seçimleri articles")
        if wiki in cs.minted:
            cs.add(
                "source",
                id=wiki,
                kind="community",
                publisher="Wikipedia",
                title="Turkish Wikipedia: per-province 2009 Türkiye yerel seçimleri articles",
                url="https://tr.wikipedia.org/wiki/2009_Türkiye_yerel_seçimleri",
                license=LICENSE,
                license_url=LICENSE_URL,
            )
        for title in pages:
            if title.endswith("2009 Türkiye yerel seçimleri"):
                cs.artifact(raw.cached(MAYORS, page(title)), wiki)

        contests: dict[UUID, tuple[UUID, UUID, UUID | None, str, list[tuple[UUID, UUID | None, str | None]]]] = {}
        for contest, election, polity, area, key, option, party, _, person_name in conn.execute(
            OPTIONS, [DAY]
        ).fetchall():
            entry = contests.setdefault(contest, (election, polity, area, key, []))
            if all(option != seen[0] for seen in entry[4]):
                entry[4].append((option, party, person_name))
        paths: dict[UUID, list[str]] = defaultdict(list)
        for entity, text in conn.execute(
            "select entity_id, path from slug where entity_id = any(%s) order by path", [list(contests)]
        ).fetchall():
            paths[entity].append(text)

        units: dict[UUID, UUID] = {}
        for area, unit in conn.execute(
            """select area_id, id from reporting_unit
               where scheme_code in ('tr.ilce', 'tr.belde') and kind = 'aggregate' and area_id is not null
                 and area_relation in ('within', 'equals') and code !~ ':'"""
        ).fetchall():
            units[area] = unit
        skipped: list[tuple[str, str, str]] = []
        result_values: list[dict[str, str | int | float | None]] = []
        full = partial = 0
        for contest, (election, polity, area, key, options) in contests.items():
            every = [text.split("/")[1:-2] for text in paths[contest]]
            parts = every[0]
            label = paths[contest][0]
            found: tuple[str, str, list[tuple[str, str, int]]] | None = None
            counts: dict[int, int] = defaultdict(int)
            for _, _, person_name in options:
                if person_name is not None:
                    for index in set(by_name.get((parts[0], fold(person_name)), [])):
                        counts[index] += 1
            best_count = max(counts.values(), default=0)
            chosen = [index for index, count in counts.items() if count == best_count]
            if best_count >= min(2, len(options)) and len(chosen) == 1:
                title, heading, _, rows = tables_2009[chosen[0]]
                found = (title, heading, rows)
            else:
                for place in every:
                    entries = beldes[2009].get((place[0], place[-1]), []) if len(place) == 3 else []
                    if len(entries) > 1:
                        entries = [entry for entry in entries if entry[2] == place[1]]
                    if len(entries) == 1:
                        entry = entries[0]
                        found = entry[0], entry[1], [(entry[3], entry[4], entry[5])]
                        break
            if found is None:
                skipped.append((str(contest), label, "no table"))
                continue
            title, heading, rows = found
            matched: dict[UUID, int] = {}
            reason = None
            for party_text, person_name, votes in rows:
                parties = party_ids.get(fold(party_text))
                if parties is None:
                    reason = f"unknown party {party_text} in {title}"
                    break
                hits = [
                    option
                    for option, party, candidate in options
                    if party in parties
                    and candidate is not None
                    and same(candidate, person_name)
                    and option not in matched
                ]
                if len(hits) != 1:
                    reason = f"{person_name} ({party_text}) matches {len(hits)} candidates in {title}"
                    break
                matched[hits[0]] = votes
            if reason is not None:
                skipped.append((str(contest), label, reason))
                continue

            unit = units.get(area)
            if unit is None:
                scheme = "tr.ilce" if len(parts) < 3 else "tr.belde"
                unit = cs.key(f"{SOURCE}.unit", key)
                units[area] = unit
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=f"2009:{'-'.join(parts)}",
                    area_relation="within",
                    area_id=area,
                    kind="aggregate",
                )
            result_set = cs.key(f"{SOURCE}.result_set", key)
            complete = len(matched) == len(options)
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
                completeness=1 if complete else 0,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            cs.add("citation", entity_id=result_set, source_id=wiki, locator={"page": title, "section": heading})
            for option, votes in matched.items():
                result_values.append(
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
            if complete:
                full += 1
            else:
                partial += 1
        cs.frame(
            "result_value",
            pl.DataFrame(
                result_values,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )

        with UNPUBLISHED.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(("contest_id", "contest", "reason"))
            skipped.sort(key=lambda row: (row[2], row[1]))
            writer.writerows(skipped)
        print(f"{full} complete, {partial} partial; {len(skipped)} unpublished")
        return cs.write()
