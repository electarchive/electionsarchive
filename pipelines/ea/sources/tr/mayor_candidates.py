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
VERSION = "1"
TITLE = "Turkish Wikipedia: per-province Türkiye yerel seçimleri articles, metropolitan mayoral tables"
CHANGES = Path(__file__).with_name("mayor_candidates_unpublished.tsv")


def races() -> tuple[dict[tuple[int, str], list[tuple[str, str, str, int]]], dict[str, str]]:
    found: dict[tuple[int, str], list[tuple[str, str, str, int]]] = {}
    pages: dict[str, str] = {}
    tables, _, _ = mayors_1984_1999.read()
    for (year, province, kind, _), (title, rows, _) in tables.items():
        if kind == "bb":
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
    return found, pages


def emit() -> Path:
    found, pages = races()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Candidates and mayors of the metropolitan mayoral contests of 1984-2004, from Turkish Wikipedia's per-province tables",
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
               where cs.pipeline = 'tr.ysk_local' and c.kind = 'office' and x.value like '%%-mayor'"""
        ).fetchall():
            contests[key] = (contest, office)
        sets = dict(
            conn.execute(
                "select k, contest_id from result_set where is_default and contest_id = any(%s)",
                [[contest for contest, _ in contests.values()]],
            ).fetchall()
        )
        tally = dict(
            conn.execute(
                """select option_k, sum(value) from public.result_value
                   where result_set_k = any(%s) and measure = %s and option_k <> 0 group by option_k""",
                [list(sets), VOTES],
            ).fetchall()
        )
        labels: dict[tuple[UUID, str], UUID | None] = {}
        for contest, option, party_id, k, label in conn.execute(
            "select contest_id, id, party_id, k, ballot_label from contest_option where contest_id = any(%s)",
            [[contest for contest, _ in contests.values()]],
        ).fetchall():
            options[contest][party_id] = (option, tally.get(k))
            labels[(contest, fold(label))] = party_id
        offices: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
        for office, person, text in conn.execute(
            """select t.office_id, t.person_id, n.text from tenure t join name n on n.entity_id = t.person_id
               where t.office_id = any(%s)""",
            [[office for _, office in contests.values()]],
        ).fetchall():
            offices[(office, fold(text))].add(person)
        held = {
            contest
            for (contest,) in conn.execute(
                """select distinct o.contest_id from candidacy ca join contest_option o on o.id = ca.option_id
                   where o.contest_id = any(%s)""",
                [[contest for contest, _ in contests.values()]],
            ).fetchall()
        }
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
            contest_key = f"{day}:{province}-mayor"
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
                if (abbreviation, label, names, votes) == leader and not tied:
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
