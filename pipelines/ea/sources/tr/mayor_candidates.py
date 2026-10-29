from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr import mayors_1984_1999, mayors_2004_2009
from ea.sources.tr.mayors_2004_2009 import LICENSE, LICENSE_URL, fold
from ea.sources.tr.ysk import VOTES, upper
from ea.sources.tr.ysk_local import DAYS, party
from ea.sources.tr.ysk_mayors import page
from ea.store import connect

SOURCE = "tr.mayor_candidates"
VERSION = "3"
TITLE = "Turkish Wikipedia: per-province Türkiye yerel seçimleri articles, metropolitan mayoral tables"
OLD = (1963, 1973)
CHANGES = Path(__file__).with_name("mayor_candidates_unpublished.tsv")


def fetch() -> None:
    mayors_1984_1999.fetch(OLD)


def races() -> tuple[
    dict[tuple[int, str], list[tuple[str, str, str, int]]],
    list[tuple[int, list[tuple[str, str, str, int]]]],
    dict[str, str],
]:
    found: dict[tuple[int, str], list[tuple[str, str, str, int]]] = {}
    municipal: list[tuple[int, list[tuple[str, str, str, int]]]] = []
    pages: dict[str, str] = {}
    tables, _, _ = mayors_1984_1999.read((*OLD, *mayors_1984_1999.YEARS))
    for (year, province, kind, _), (title, rows, _) in tables.items():
        if year in OLD:
            municipal.append((year, [(abbreviation, label, names, votes) for abbreviation, label, names, votes in rows]))
            pages[title] = mayors_1984_1999.SOURCE
        elif kind == "bb":
            found[(year, mayors_1984_1999.PROVINCES.get(province, province))] = [
                (abbreviation, label, names, votes) for abbreviation, label, names, votes in rows
            ]
            pages[title] = mayors_1984_1999.SOURCE
    tables_2004, _, _ = mayors_2004_2009.read_tables((2004,))
    for title, heading, province, rows in tables_2004[2004]:
        if "buyuksehir" in fold(heading):
            found[(2004, mayors_1984_1999.PROVINCES.get(province, province))] = [
                (label, label, names, votes) for label, names, votes in rows
            ]
            pages[title] = mayors_2004_2009.MAYORS
        else:
            municipal.append((2004, [(label, label, names, votes) for label, names, votes in rows]))
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
               where cs.pipeline in ('tr.ysk_local', 'tr.ysk_mayors', 'tr.die_local') and c.kind = 'office'
                 and x.value ~ '^(1963-11-17|1973-12-09|1984-03-25|1989-03-26|1994-03-27|1999-04-18|2004-03-28):.*-mayor$'"""
        ).fetchall():
            contests[key] = (contest, office)
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
        for contest, option, party_id, k, label in conn.execute(
            "select contest_id, id, party_id, k, ballot_label from contest_option where contest_id = any(%s)",
            [[contest for contest, _ in contests.values()]],
        ).fetchall():
            options[contest][party_id] = (option, tally.get(k))
            option_party[option] = party_id
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
                "select contest_id, person_id from tenure where contest_id = any(%s)",
                [[contest for contest, _ in contests.values()]],
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
        index: dict[tuple[int, tuple[int, ...]], list[str]] = defaultdict(list)
        for key, (contest, _) in contests.items():
            if contest not in held and by_votes[contest] and int(key[:4]) in (*OLD, 2004):
                index[(int(key[:4]), tuple(sorted(by_votes[contest], reverse=True)[:3]))].append(key)
        for year, rows in municipal:
            hits = index.get((year, tuple(sorted((row[3] for row in rows), reverse=True)[:3])), [])
            if len(rows) > 1 and len(hits) == 1:
                found[(year, hits[0])] = rows
        offices: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
        for office, person, text in conn.execute(
            """select t.office_id, t.person_id, n.text from tenure t join name n on n.entity_id = t.person_id
               where t.office_id = any(%s)""",
            [[office for _, office in contests.values()]],
        ).fetchall():
            offices[(office, fold(text))].add(person)
        known: dict[str, set[UUID]] = defaultdict(set)
        national: dict[str, set[UUID]] = defaultdict(set)
        for person, text, tbmm in conn.execute(
            """select n.entity_id, n.text, exists (select 1 from tenure t where t.person_id = n.entity_id
                      and t.body_id = (select entity_id from external_id where scheme = 'tr.body' and value = 'tbmm'))
               from name n where n.entity_id in (
                   select t.person_id from tenure t join changeset cs on cs.id = t.changeset_id
                   where cs.pipeline like 'tr.%%')"""
        ).fetchall():
            known[fold(text)].add(person)
            if tbmm:
                national[fold(text)].add(person)

        skipped: list[tuple[str, str, str]] = []
        made = 0
        for (year, province), rows in sorted(found.items()):
            day = DAYS[year]
            contest_key = province if ":" in province else f"{day}:{province}-mayor"
            if contest_key not in contests:
                skipped.append((str(year), province, "no contest"))
                continue
            contest, office = contests[contest_key]
            if contest in held:
                skipped.append((str(year), province, "contest already has candidacies"))
                continue
            leader = max(rows, key=lambda row: row[3])
            tied = sum(1 for row in rows if row[3] == leader[3]) > 1
            for abbreviation, label, names, votes in rows:
                option = by_votes[contest].get(votes)
                if option is not None:
                    party_id = option_party[option]
                    existing = votes
                else:
                    independent = fold(abbreviation).startswith(("bgmsz", "bagimsiz")) or fold(names) == ""
                    owner = None
                    if not independent:
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
                name = names.split("; ")[0].strip()
                matches = offices.get((office, fold(name))) or national.get(fold(name)) or known.get(fold(name), set())
                is_winner = (abbreviation, label, names, votes) == leader and not tied
                if is_winner and winners.get(contest) is not None:
                    matches = {winners[contest]}
                if len(matches) == 1:
                    person = next(iter(matches))
                else:
                    person = cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(name)}")
                    if len(matches) > 1:
                        skipped.append((str(year), province, f"{name}: {len(matches)} namesakes, new person"))
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=name, lang="tr", kind="official", is_preferred=True)
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
                        candidacy_id=candidacy,
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
                    cs.add("citation", entity_id=tenure, source_id=wiki, locator={"contest": contest_key})
        with CHANGES.open("w", encoding="utf-8") as handle:
            handle.write("year\tprovince\treason\n")
            for row in skipped:
                handle.write("\t".join(row) + "\n")
        print(f"{made} candidacies; {len(skipped)} unpublished")
        return cs.write()
