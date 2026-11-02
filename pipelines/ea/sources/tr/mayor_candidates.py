from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.tr import mayors_1984_1999, mayors_2004_2009
from ea.sources.tr.mayors_1984_1999 import Row
from ea.sources.tr.mayors_2004_2009 import LICENSE, LICENSE_URL, fold
from ea.sources.tr.ysk import PROVINCES as PLATES
from ea.sources.tr.ysk import VOTES, upper
from ea.sources.tr.ysk_local import DAYS, party
from ea.sources.tr.ysk_mayors import page
from ea.store import connect

SOURCE = "tr.mayor_candidates"
VERSION = "14"
TITLE = "Turkish Wikipedia: per-province Türkiye yerel seçimleri articles, metropolitan mayoral tables"
OLD = (1963, 1973)
CHANGES = Path(__file__).with_name("mayor_candidates_unpublished.tsv")


def independent(abbreviation: str, label: str, names: str) -> bool:
    return fold(abbreviation or label).startswith(("bgmsz", "bagimsiz")) or fold(names) == ""


def fresh(cs: Changeset, value: str) -> UUID:
    scheme = f"{SOURCE}.person"
    while uuid5(NAMESPACE, f"external_id:{scheme}\x1f{value}") in cs.retired:
        value = f"{value}:again"
    return cs.key(scheme, value)


def fetch() -> None:
    mayors_1984_1999.fetch()


def ranked(rows: list[tuple[str, str, str, int]]) -> list[Row]:
    top = max(votes for _, _, _, votes in rows)
    alone = sum(1 for _, _, _, votes in rows if votes == top) == 1
    return [
        (abbreviation, label, names, votes, alone and votes == top and "; " not in names)
        for abbreviation, label, names, votes in rows
    ]


def races() -> tuple[dict[tuple[int, str], list[Row]], list[tuple[int, list[Row]]], dict[str, str]]:
    found: dict[tuple[int, str], list[Row]] = {}
    municipal: list[tuple[int, list[Row]]] = []
    pages: dict[str, str] = {}
    tables, beldes_old, _, _ = mayors_1984_1999.read()
    for (year, province, kind, _), (title, rows, _) in tables.items():
        if year in OLD:
            municipal.append((year, rows))
            pages[title] = mayors_1984_1999.SOURCE
        elif kind == "bb":
            found[(year, mayors_1984_1999.PROVINCES.get(province, province))] = rows
            pages[title] = mayors_1984_1999.SOURCE
    tables_2004, beldes_2004, _ = mayors_2004_2009.read_tables((2004,))
    for title, heading, province, rows in tables_2004[2004]:
        if "buyuksehir" in fold(heading):
            found[(2004, mayors_1984_1999.PROVINCES.get(province, province))] = ranked(
                [(label, label, names, votes) for label, names, votes in rows]
            )
            pages[title] = mayors_2004_2009.MAYORS
        else:
            municipal.append((2004, ranked([(label, label, names, votes) for label, names, votes in rows])))
            pages[title] = mayors_2004_2009.MAYORS
    for (year, province, district, belde), rows in beldes_old.items():
        if year in OLD and len(rows) == 1:
            title, label, names, votes = rows[0]
            found[(year, f"{DAYS[year]}:{province}-{slug(district)}-{slug(belde)}-mayor")] = ranked(
                [("", label, names, votes)]
            )
            pages[title] = mayors_1984_1999.SOURCE
    for (province, belde), rows in beldes_2004[2004].items():
        if len(rows) != 1:
            continue
        title, _, district, label, names, votes = rows[0]
        found[(2004, f"{DAYS[2004]}:{province}-{'merkez' if district == province else district}-{belde}-mayor")] = (
            ranked([("", label, names, votes)])
        )
        pages[title] = mayors_2004_2009.MAYORS
    return found, municipal, pages


def emit() -> Path:
    found, municipal, pages = races()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Candidates and mayors of the mayoral contests of 1984-2004 (metropolitan, and municipal in 2004), from Turkish Wikipedia's per-province tables",
            polity="TR",
        )
        wiki = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=wiki,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://tr.wikipedia.org/wiki/1994_Türkiye_yerel_seçimleri",
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        for title, origin in pages.items():
            cs.artifact(raw.cached(origin, page(title)), wiki)

        contests: dict[str, tuple[UUID, UUID]] = {}
        options: dict[UUID, dict[UUID | None, tuple[UUID, int | None]]] = defaultdict(dict)
        for key, contest, office in conn.execute(
            """select x.value, c.id, c.office_id from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'tr.contest'
               join changeset cs on cs.id = c.changeset_id
               where cs.pipeline in ('tr.ysk_local', 'tr.ysk_mayors', 'tr.die_local', 'tr.mayors_1984_1999') and c.kind = 'office'
                 and x.value ~ '^(1963-11-17|1973-12-09|1984-03-25|1989-03-26|1994-03-27|1999-04-18|2004-03-28):.*-mayor$'
               order by x.changeset_id, x.value"""
        ).fetchall():
            contests[key] = (contest, office)
        stable: dict[UUID, str] = {}
        for contest, value in conn.execute(
            """select o.contest_id, x.value from candidacy ca join contest_option o on o.id = ca.option_id
               join external_id x on x.entity_id = ca.id and x.scheme = 'tr.candidacy'
               join changeset cs on cs.id = ca.changeset_id
               where cs.pipeline = %s and o.contest_id = any(%s) order by x.value""",
            [SOURCE, [contest for contest, _ in contests.values()]],
        ).fetchall():
            if value.rsplit(":", 2)[0] in contests:
                stable.setdefault(contest, value.rsplit(":", 2)[0])
        primary = {contest: key for key, (contest, _) in contests.items()}
        for contest, key in stable.items():
            if key != primary[contest]:
                cs.renamed[primary[contest]] = key
        declared: dict[UUID, UUID] = {}
        sets: dict[int, UUID] = {}
        for k, set_id, contest in conn.execute(
            "select k, id, contest_id from result_set where is_default and contest_id = any(%s)",
            [[contest for contest, _ in contests.values()]],
        ).fetchall():
            sets[k] = contest
            declared[contest] = set_id
        decided = {
            result_set
            for (result_set,) in conn.execute(
                """select o.result_set_id from outcome o join changeset cs on cs.id = o.changeset_id
                   where o.result_set_id = any(%s) and cs.pipeline <> %s""",
                [list(declared.values()), SOURCE],
            ).fetchall()
        }
        tally = dict(
            conn.execute(
                """select option_k, sum(value) from public.result_value
                   where result_set_k = any(%s) and measure = %s and option_k <> 0 group by option_k""",
                [list(sets), VOTES],
            ).fetchall()
        )
        labels: dict[tuple[UUID, str], UUID | None] = {}
        option_party: dict[UUID, UUID | None] = {}
        heads: set[UUID] = set()
        for contest, option, party_id, k, label, kind in conn.execute(
            "select contest_id, id, party_id, k, ballot_label, kind::text from contest_option where contest_id = any(%s)",
            [[contest for contest, _ in contests.values()]],
        ).fetchall():
            options[contest][party_id] = (option, tally.get(k))
            option_party[option] = party_id
            if kind in ("independents", "others"):
                heads.add(option)
            labels[(contest, fold(label))] = party_id
        by_votes: dict[UUID, dict[int, UUID]] = defaultdict(dict)
        for contest, option, k in conn.execute(
            "select contest_id, id, k from contest_option where contest_id = any(%s)",
            [[contest for contest, _ in contests.values()]],
        ).fetchall():
            if tally.get(k) is not None:
                by_votes[contest][int(tally[k])] = option
        winners = dict(
            conn.execute(
                """select t.contest_id, t.person_id from tenure t join changeset cs on cs.id = t.changeset_id
                   where t.contest_id = any(%s) and cs.pipeline <> %s
                     and not exists (select from external_id x where x.entity_id = t.id and x.scheme = %s)""",
                [[contest for contest, _ in contests.values()], SOURCE, f"{SOURCE}.tenure"],
            ).fetchall()
        )
        held = {
            contest
            for (contest,) in conn.execute(
                """select distinct o.contest_id from candidacy ca join contest_option o on o.id = ca.option_id
                   join changeset cs on cs.id = ca.changeset_id
                   where o.contest_id = any(%s) and cs.pipeline <> %s""",
                [[contest for contest, _ in contests.values()], SOURCE],
            ).fetchall()
        }
        index: dict[tuple[int, int], list[str]] = defaultdict(list)
        for key, (contest, _) in contests.items():
            if primary[contest] == key and contest not in held and by_votes[contest] and int(key[:4]) in (*OLD, 2004):
                index[(int(key[:4]), max(by_votes[contest]))].append(key)
        taken = {key for (_, key) in found}
        placed: set[int] = set()
        for loose in (False, True):
            claimed: Counter[str] = Counter()
            matched: dict[tuple[int, str], tuple[int, list[Row]]] = {}
            for table, (year, rows) in enumerate(municipal):
                votes = {row[3] for row in rows if row[3] > 0}
                parties = {row[3] for row in rows if row[3] > 0 and not independent(*row[:3])}
                hits = [
                    key
                    for key in index.get((year, max(votes, default=0)), [])
                    if key not in taken
                    and (
                        len(parties) > 1
                        and len(parties - set(by_votes[contests[key][0]])) <= (1 if len(parties) > 2 else 0)
                        if loose
                        else len(votes) > 1 and votes <= set(by_votes[contests[key][0]])
                    )
                ]
                if table not in placed and len(hits) == 1:
                    matched[(year, hits[0])] = (table, rows)
                    claimed[hits[0]] += 1
            for (year, key), (table, rows) in matched.items():
                if claimed[key] == 1:
                    found[(year, key)] = rows
                    taken.add(key)
                    placed.add(table)
        offices: dict[tuple[UUID, str], set[tuple[UUID, UUID | None, int | None]]] = defaultdict(set)
        for office, person, text, race, year in conn.execute(
            """select t.office_id, t.person_id, n.text, t.contest_id, null::int from tenure t
               join name n on n.entity_id = t.person_id join changeset ch on ch.id = t.changeset_id
               where t.office_id = any(%s) and ch.pipeline <> %s
               union
               select c.office_id, ca.person_id, n.text, c.id, extract(year from e.date_from)::int from candidacy ca
               join name n on n.entity_id = ca.person_id join changeset ch on ch.id = ca.changeset_id
               join contest_option co on co.id = ca.option_id join contest c on c.id = co.contest_id
               join election e on e.id = c.election_id
               where c.office_id = any(%s) and ch.pipeline <> %s""",
            [[office for _, office in contests.values()], SOURCE] * 2,
        ).fetchall():
            offices[(office, fold(text))].add((person, race, year))
        provinces = {f"{plate:02d}": slug(text) for plate, text in enumerate(PLATES, 1)}
        known: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        for person, text, places, seats in conn.execute(
            """select n.entity_id, n.text,
                      array(select distinct split_part(s.path, '/', 2) from tenure t join office o on o.id = t.office_id
                            join slug s on s.entity_id = o.polity_id and s.is_current
                            join changeset ch on ch.id = t.changeset_id
                            where t.person_id = n.entity_id and ch.pipeline <> %s),
                      array(select distinct split_part(x.value, ':', 3) from tenure t
                            join external_id x on x.entity_id = t.contest_id and x.scheme = 'tr.contest'
                            where t.person_id = n.entity_id and x.value like '%%:parliament:%%')
               from name n where n.entity_id in (
                   select t.person_id from tenure t join changeset cs on cs.id = t.changeset_id
                   where cs.pipeline like 'tr.%%' and cs.pipeline <> %s)""",
            [SOURCE, SOURCE],
        ).fetchall():
            for place in {*places, *(provinces.get(seat[:2], seat) for seat in seats)}:
                known[(place, fold(text))].add(person)
        busy: dict[tuple[UUID, int], set[UUID]] = defaultdict(set)
        for person, year, office in conn.execute(
            """select ca.person_id, extract(year from e.date_from)::int, c.office_id from candidacy ca
               join changeset ch on ch.id = ca.changeset_id join contest_option co on co.id = ca.option_id
               join contest c on c.id = co.contest_id join election e on e.id = c.election_id
               join office o on o.id = c.office_id and o.kind = 'mayor'
               where ch.pipeline like 'tr.%%' and ch.pipeline <> %s""",
            [SOURCE],
        ).fetchall():
            busy[(person, year)].add(office)

        skipped: list[tuple[str, str, str]] = []
        made = 0
        entries: list[tuple[int, str, UUID, UUID, str, str, str, UUID, UUID | None, bool]] = []
        chosen: list[set[UUID]] = []
        names_of: dict[UUID, list[str]] = defaultdict(list)
        by_belde: dict[tuple[str, str], list[str]] = defaultdict(list)
        for key in contests:
            if contests[key][0] not in held:
                day, rest = key.split(":", 1)
                by_belde[day, rest.removesuffix("-mayor").split("-")[-1]].append(key)
        wanted = {key for (_, key) in found}
        for (year, province), rows in sorted(found.items()):
            day = DAYS[year]
            contest_key = province if ":" in province else f"{day}:{province}-mayor"
            if contest_key not in contests and ":" in province:
                tokens = province.removeprefix(f"{day}:").removesuffix("-mayor").split("-")
                hits = {
                    contests[key][0]: key
                    for key in by_belde[str(day), tokens[-1]]
                    if key not in wanted
                    and (
                        key.split(":", 1)[1].startswith(tokens[0])
                        or tokens[0].startswith(key.split(":", 1)[1].split("-")[0])
                    )
                }
                if len(hits) == 1:
                    contest_key = next(iter(hits.values()))
            if contest_key not in contests:
                skipped.append((str(year), province, "no contest"))
                continue
            contest, office = contests[contest_key]
            contest_key = primary[contest]
            if contest in held:
                skipped.append((str(year), province, "contest already has candidacies"))
                continue
            for abbreviation, label, names, votes, won in rows:
                option = by_votes[contest].get(votes)
                if option is not None:
                    party_id = option_party[option]
                    existing = votes
                else:
                    owner = None
                    if not independent(abbreviation, label, names):
                        for candidate in (label, upper(label), abbreviation):
                            try:
                                owner = party(candidate, year)
                            except KeyError:
                                owner = mayors_1984_1999.EXTRA.get(abbreviation)
                            if owner is not None:
                                break
                        if owner is None:
                            skipped.append((str(year), province, f"unknown party {abbreviation}"))
                            continue
                    party_id = None if owner is None else cs.key("tr.party", owner)
                    if party_id is not None and party_id not in options[contest]:
                        party_id = labels.get((contest, fold(label)), party_id)
                    option, existing = options[contest].get(party_id, (None, None))
                    if option is None:
                        if year != 2004:
                            skipped.append((str(year), province, f"no option for {abbreviation}"))
                        continue
                    if existing is not None and existing != votes:
                        skipped.append((str(year), province, f"{abbreviation}: {votes} against {existing}"))
                        continue
                pooled = fold(abbreviation or label).startswith(("bgmsz", "bagimsiz"))
                people = [part.strip() for part in names.split("; ") if fold(part) not in ("", "bilinmeyenadaylar")]
                if not people and not pooled:
                    people = [names.split("; ")[0].strip()]
                for name in people:
                    unknown = fold(name) in ("", "bilinmiyor")
                    region = contest_key.split(":", 1)[1].split("-")[0]
                    nearby = {
                        region,
                        *mayors_1984_1999.SUCCESSORS.get(region, ()),
                        *(parent for parent, kids in mayors_1984_1999.SUCCESSORS.items() if region in kids),
                    }
                    matches: set[UUID] = set()
                    for form in () if unknown else mayors_1984_1999.also(name):
                        if form is not None and not matches:
                            matches = {
                                person
                                for person, race, held in offices.get((office, fold(form)), set())
                                if race != contest and (held is None or abs(held - year) <= 7)
                            } or {person for place in nearby for person in known.get((place, fold(form)), set())}
                    is_winner = won and name == people[0]
                    if is_winner and winners.get(contest) is not None:
                        matches = {winners[contest]}
                    entries.append(
                        (year, province, contest, office, contest_key, abbreviation, name, option, party_id, is_winner)
                    )
                    chosen.append(matches)
                    if len(matches) == 1:
                        busy[(next(iter(matches)), year)].add(office)
        for (
            year,
            province,
            contest,
            office,
            contest_key,
            abbreviation,
            name,
            option,
            party_id,
            is_winner,
        ), matches in zip(entries, chosen, strict=True):
            unknown = fold(name) in ("", "bilinmiyor")
            if (
                len(matches) == 1
                and len(busy[(next(iter(matches)), year)]) > 1
                and not (is_winner and winners.get(contest) is not None)
            ):
                skipped.append((str(year), province, f"{name}: matched to a mayor elsewhere that year, new person"))
                matches = set()
            if len(matches) == 1:
                person = next(iter(matches))
            else:
                person = fresh(
                    cs, f"{contest_key}:{slug(abbreviation)}:{slug(name)}" if unknown else f"{contest_key}:{slug(name)}"
                )
                if len(matches) > 1:
                    skipped.append((str(year), province, f"{name}: {len(matches)} namesakes, new person"))
            names_of[person].append(name)
            if person in cs.minted:
                cs.add("person", id=person)
                mayors_1984_1999.named(cs, person, name)
            candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(abbreviation)}:{slug(name)}")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                member_of_party_id=party_id,
                is_independent=party_id is None,
            )
            if party_id is not None:
                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party_id, kind="nominated")
            made += 1
            if is_winner and declared.get(contest) not in (None, *decided):
                cs.add(
                    "outcome",
                    result_set_id=declared[contest],
                    option_id=option,
                    candidacy_id=candidacy if option in heads else None,
                    kind="elected",
                    seats=None,
                )
            if is_winner and contest not in winners:
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
                if party_id is not None:
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=party_id,
                        valid_from=start,
                        valid_from_precision="month",
                        valid_to=end,
                        valid_to_precision="month",
                    )
                cs.add(
                    "citation",
                    entity_id=tenure,
                    source_id=wiki,
                    locator={"contest": stable.get(contest, contest_key)},
                )
        restaged = [str(row["id"]) for table in ("candidacy", "tenure") for row in cs.rows.get(table, [])]
        referenced = {str(row["person_id"]) for table in ("candidacy", "tenure") for row in cs.rows.get(table, [])}
        stale = conn.execute(
            """select 'candidacy', c.id::text, null::uuid from candidacy c join changeset ch on ch.id = c.changeset_id
               where ch.pipeline = %s and c.id <> all(%s::uuid[])
               union all
               select 'tenure', t.id::text, t.contest_id from tenure t join changeset ch on ch.id = t.changeset_id
               where ch.pipeline = %s and t.id <> all(%s::uuid[])""",
            [SOURCE, restaged, SOURCE, restaged],
        ).fetchall()
        for table, entity, race in stale:
            cs.add(
                "entity_retirement",
                entity_id=entity,
                reason="A candidacy read from a misparsed Wikipedia name cell, replaced by the candidates the cell names."
                if table == "candidacy"
                else "A term another source already records for this contest."
                if race in winners
                else "A term given to an independent, though the pooled independent votes do not belong to any one candidate.",
            )
        mayors_1984_1999.rename(cs, SOURCE, names_of)
        for (person,) in conn.execute(
            """select p.id::text from person p join changeset ch on ch.id = p.changeset_id
               where ch.pipeline = %s and not exists (select from entity_merge m where m.entity_id = p.id)
                 and not exists (select from candidacy c where c.person_id = p.id and c.id <> all(%s::uuid[]))
                 and not exists (select from tenure t where t.person_id = p.id and t.id <> all(%s::uuid[]))""",
            [SOURCE, restaged + [entity for _, entity, _ in stale], restaged + [entity for _, entity, _ in stale]],
        ).fetchall():
            if person not in referenced:
                cs.add(
                    "entity_retirement",
                    entity_id=person,
                    reason="A person read from the Wikipedia article whom the archive no longer links to any candidacy or term.",
                )
        with CHANGES.open("w", encoding="utf-8") as handle:
            handle.write("year\tprovince\treason\n")
            for row in skipped:
                handle.write("\t".join(row) + "\n")
        print(f"{made} candidacies; {len(skipped)} unpublished")
        return cs.write()
