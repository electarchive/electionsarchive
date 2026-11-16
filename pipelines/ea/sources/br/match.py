import json
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br import tse_early
from ea.store import DATA, connect

SOURCE = "br.match"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
YOUNGEST = 21
OLDEST = 75
REDEMOCRATIZATION = 1982
CONSECUTIVE = 12
STATEWIDE = ("br.tse_history", "br.tse")
SPELLED = {"juscelino-kubistcheck-de-oliveira": "juscelino-kubitschek-de-oliveira"}
NAMESAKES = {
    "antonio-dos-santos-pedreira|AC",
    "antonio-dos-santos-pedreira|AP",
    "antonio-dos-santos-pedreira|GB",
    "antonio-dos-santos-pedreira|RJ",
    "armando-correa-da-silva|MG",
    "eduardo-gomes|RO",
}
POSITIONS = {
    "chamber-of-deputies": ("Q20058725", 4),
    "senate": ("Q18964326", 8),
    "assembly": ("Q10265290", 4),
    "governor": ("Q25921469", 5),
}
HELD = """SELECT ?item ?uf ?start ?end ?birth WHERE {{ ?position wdt:P279* wd:{top} . ?item p:P39 ?held .
?held ps:P39 ?position . OPTIONAL {{ ?held pq:P580 ?start }} OPTIONAL {{ ?held pq:P582 ?end }}
OPTIONAL {{ ?item wdt:P569 ?birth }} OPTIONAL {{ ?position wdt:P1001 ?state . ?state wdt:P300 ?uf }} }}"""
LABELS = """SELECT DISTINCT ?item ?name WHERE {{ ?position wdt:P279* wd:{top} . ?item wdt:P39 ?position .
{{ ?item rdfs:label ?name }} UNION {{ ?item skos:altLabel ?name }} UNION {{ ?item wdt:P1477 ?name }}
FILTER(LANG(?name) IN ("pt", "pt-br", "en")) }}"""
TITLE = "Brazilian deputies, senators, and governors: positions held (P39), birth dates, labels, and aliases"


def _url(query: str, top: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode({"query": query.format(top=top), "format": "json"})


def fetch() -> None:
    for top, _ in POSITIONS.values():
        for query in (HELD, LABELS):
            raw.fetch(SOURCE, _url(query, top), suffix=".json")


def _bindings(query: str, top: str) -> list[dict[str, str]]:
    document = json.loads(raw.cached(SOURCE, _url(query, top)).path.read_text(encoding="utf-8"))
    return [{key: value["value"] for key, value in row.items()} for row in document["results"]["bindings"]]


def _candidacies(conn) -> pl.DataFrame:
    return pl.DataFrame(
        conn.execute(
            """
            select cy.person_id::text, s.pipeline, x.value,
              case when s.pipeline = 'br.municipais' then (
                select pc.code from area_parent ap
                join area pa on pa.id = ap.parent_id and pa.scheme_code = 'br.uf'
                join area_code pc on pc.area_id = pa.id
                where ap.area_id = c.district_area_id limit 1
              ) end,
              (select n.text from name n where n.entity_id = cy.member_of_party_id and n.kind = 'abbreviation' limit 1),
              exists (select from outcome w join result_set r on r.id = w.result_set_id and r.is_default
                      where w.option_id = o.id and w.kind = 'elected'),
              cy.ballot_name
            from external_id x
            join changeset s on s.id = x.changeset_id
            join candidacy cy on cy.id = x.entity_id
            join contest_option o on o.id = cy.option_id
            join contest c on c.id = o.contest_id
            where x.scheme = 'br.candidacy'
            """
        ).fetchall(),
        schema={
            "person": pl.String,
            "pipeline": pl.String,
            "key": pl.String,
            "town_uf": pl.String,
            "party": pl.String,
            "elected": pl.Boolean,
            "ballot": pl.String,
        },
        orient="row",
    )


def _items() -> tuple[dict[str, list[tuple[str, str | None, int, int]]], dict[str, set[int]], dict[str, set[str]]]:
    held: dict[str, list[tuple[str, str | None, int, int]]] = defaultdict(list)
    born: dict[str, set[int]] = defaultdict(set)
    called: dict[str, set[str]] = defaultdict(set)
    for office, (top, _) in POSITIONS.items():
        for row in _bindings(HELD, top):
            item = row["item"].rsplit("/", 1)[1]
            start, end = row.get("start", "")[:4], row.get("end", "")[:4]
            held[item].append(
                (
                    office,
                    row["uf"].removeprefix("BR-") if "uf" in row else None,
                    int(start) if start.isdigit() else -9999,
                    int(end) if end.isdigit() else 9999,
                )
            )
            if row.get("birth", "")[:4].isdigit():
                born[item].add(int(row["birth"][:4]))
        for row in _bindings(LABELS, top):
            called[tse_early.slug(row["name"])].add(row["item"].rsplit("/", 1)[1])
    return held, born, called


def cross_state(
    careers: list[tuple[str, str, int, int, int, set[str], set[str]]],
    births: dict[str, set[int]],
    items: dict[str, set[str]],
) -> dict[str, str]:
    """Join persons who share a full name across states when the name is unique nationwide among plausible ages.

    Each career is (person, name slug, first year, last year, candidacies, parties at the first race, parties at the
    last race). A name's persons merge only when, ordered by first year, each career ends before the next begins and
    within CONSECUTIVE years, every neighbouring pair shares a Wikidata item or a party between the earlier person's
    last race and the later person's first, and no two persons hold different items or different birth years. A name
    with any other plausible namesake, or any pair that fails, is left apart.
    """
    by_name: dict[str, list[tuple[str, str, int, int, int, set[str], set[str]]]] = defaultdict(list)
    for career in careers:
        by_name[career[1]].append(career)
    merges: dict[str, str] = {}
    for group in by_name.values():
        anchor = min(career[2] for career in group)
        group = [
            career for career in group if all(YOUNGEST <= anchor - born <= OLDEST for born in births.get(career[0], ()))
        ]
        if len(group) < 2:
            continue
        group.sort(key=lambda career: (career[2], career[3]))
        people = [career[0] for career in group]
        if any(before[3] >= after[2] or after[2] - before[3] > CONSECUTIVE for before, after in pairwise(group)):
            continue
        held = [items.get(person, set()) for person in people]
        if any(a and b and not a & b for i, a in enumerate(held) for b in held[i + 1 :]):
            continue
        known = [births[person] for person in people if births.get(person)]
        if any(not a & b for i, a in enumerate(known) for b in known[i + 1 :]):
            continue
        if not all(held[i] & held[i + 1] or before[6] & after[5] for i, (before, after) in enumerate(pairwise(group))):
            continue
        survivor = min(group, key=lambda career: (not items.get(career[0]), -career[4], career[0]))[0]
        for person in people:
            if person != survivor:
                merges[person] = survivor
    return merges


def emit() -> Path:
    with connect() as conn:
        merged = dict(
            conn.execute(
                """select m.entity_id::text, m.into_id::text from entity_merge m
                   join person p on p.id = m.entity_id"""
            ).fetchall()
        )

        def root(person: str) -> str:
            while person in merged:
                person = merged[person]
            return person

        candidacies = _candidacies(conn)
        roots = pl.DataFrame({"person": candidacies["person"].unique()}).with_columns(
            root=pl.col("person").map_elements(root, return_dtype=pl.String)
        )
        candidacies = candidacies.join(roots, on="person").with_columns(
            year=pl.col("key").str.slice(0, 4).cast(pl.Int32),
            uf=pl.when(pl.col("pipeline") == "br.municipais")
            .then(pl.col("town_uf"))
            .otherwise(pl.col("key").str.split(":").list.get(2)),
            ballot=pl.col("ballot").map_elements(tse_early.slug, return_dtype=pl.String),
        )
        names = (
            pl.DataFrame(
                conn.execute(
                    """select p.id::text, p.birth_date, n.text from person p
                       join name n on n.entity_id = p.id and n.kind = 'official'
                       where p.id = any(%s::uuid[])""",
                    [roots["person"].to_list()],
                ).fetchall(),
                schema={"person": pl.String, "birth": pl.Date, "name": pl.String},
                orient="row",
            )
            .join(roots, on="person")
            .select("root", "birth", slug=pl.col("name").map_elements(tse_early.slug, return_dtype=pl.String))
            .unique()
        )
        later = candidacies.filter((pl.col("pipeline") != tse_early.SOURCE) & (pl.col("year") >= 1994))
        early = candidacies.filter(
            (pl.col("pipeline") == tse_early.SOURCE) & ~pl.col("root").is_in(later["root"].unique().implode())
        )
        early_people = (
            early.group_by("root", "uf")
            .agg(
                first=pl.col("year").min(),
                last=pl.col("year").max(),
                won=pl.col("elected").any(),
                ballots=pl.col("ballot").drop_nulls().unique(),
                parties=pl.col("party").drop_nulls().unique(),
            )
            .join(names.select("root", "slug").unique(), on="root")
        )
        later_people = (
            later.group_by("root")
            .agg(
                later_first=pl.col("year").min(),
                later_won=pl.col("elected").any(),
                later_ballots=pl.col("ballot").drop_nulls().unique(),
                later_parties=pl.col("party").drop_nulls().unique(),
                statewide=pl.col("pipeline").is_in(STATEWIDE).any(),
            )
            .rename({"root": "later"})
        )
        births = names.group_by("root").agg(pl.col("birth").drop_nulls().unique()).rename({"root": "later"})
        pairs = (
            early_people.join(
                later.select("root", "uf").unique().join(names.select("root", "slug").unique(), on="root"),
                on=["slug", "uf"],
            )
            .rename({"root_right": "later"})
            .join(later_people, on="later")
            .join(births, on="later")
            .with_columns(
                age=pl.when(pl.col("birth").list.len() == 1).then(
                    pl.col("first") - pl.col("birth").list.first().dt.year()
                ),
                ballot=pl.col("ballots").list.set_intersection(pl.col("later_ballots")).list.len() > 0,
                party=pl.col("parties").list.set_intersection(pl.col("later_parties")).list.len() > 0,
            )
            .with_columns(plausible=pl.col("age").is_between(YOUNGEST, OLDEST).fill_null(False))
        )
        options = pairs.group_by("root").agg(
            namesakes=pl.col("later").filter(pl.col("plausible")).n_unique(),
            unknown=pl.col("later").filter(pl.col("age").is_null()).n_unique(),
        )
        recent = pl.col("last") >= REDEMOCRATIZATION
        evidence = (
            pl.when(pl.col("statewide"))
            .then(
                pl.col("party")
                | (
                    recent
                    & (
                        pl.col("ballot")
                        | (pl.col("later_first") - pl.col("last") <= 4)
                        | (pl.col("won") & pl.col("later_won"))
                    )
                )
            )
            .otherwise(recent & pl.col("ballot") & pl.col("party"))
        )
        chosen = (
            pairs.join(options, on="root")
            .filter(pl.col("plausible") & (pl.col("namesakes") == 1) & (pl.col("unknown") == 0) & evidence)
            .sort("root", "later")
        )
        PARSED.mkdir(parents=True, exist_ok=True)
        pairs.join(chosen.select("root", "later", chosen=pl.lit(True)), on=["root", "later"], how="left").select(
            "root", "uf", "slug", "first", "last", "later", "age", "ballot", "party", "statewide", "chosen"
        ).write_parquet(PARSED / "pairs.parquet")
        print(
            f"{early_people['root'].n_unique()} early people, {pairs['root'].n_unique()} with a later namesake "
            f"in their state, {chosen['root'].n_unique()} matched to {chosen['later'].n_unique()} later people"
        )
        survivors = dict(chosen.select("root", "later").iter_rows())
        keyed = conn.execute(
            "select value, entity_id::text from external_id where scheme = %s", [f"{tse_early.SOURCE}.person"]
        ).fetchall()
        statewide = defaultdict(list)
        for value, entity in keyed:
            if "|" in value and value not in NAMESAKES:
                statewide[value.split("|")[0]].append(root(entity))
        returning = set(later["root"].unique())
        for value, entity in keyed:
            if "|" in value or not statewide.get(SPELLED.get(value, value)):
                continue
            group = {survivors.get(person, person) for person in [root(entity), *statewide[SPELLED.get(value, value)]]}
            survivor = min(group, key=lambda person: (person not in returning, person))
            for person in group - {survivor}:
                survivors[person] = survivor

        def settle() -> None:
            for person in list(survivors):
                while survivors[person] in survivors:
                    survivors[person] = survivors[survivors[person]]

        settle()
        held, born, called = _items()
        taken = {
            value: root(entity)
            for value, entity in conn.execute(
                "select value, entity_id::text from external_id where scheme = 'wikidata'"
            ).fetchall()
        }
        holding = defaultdict(set)
        for value, entity in taken.items():
            holding[entity].add(value)
        wins = early.filter(pl.col("elected")).with_columns(office=pl.col("key").str.split(":").list.get(1))
        spellings = pl.concat(
            [
                names.select("root", "slug"),
                wins.select("root", slug="ballot").filter(pl.col("slug").str.contains("-")),
            ]
        ).unique()
        terms_of: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for person, office, uf, year in wins.select("root", "office", "uf", "year").unique().iter_rows():
            terms_of[person].append((office, uf, year))
        slugs_of = {
            person: slugs
            for person, slugs in spellings.join(wins.select("root").unique(), on="root")
            .group_by("root")
            .agg("slug")
            .iter_rows()
        }

        def claim() -> dict[str, set[str]]:
            birth_years = {
                person: {day.year for day in days}
                for person, days in births.iter_rows()
                if person in survivors.values() or person in survivors
            }
            found_by: dict[str, set[str]] = defaultdict(set)
            for person, slugs in slugs_of.items():
                terms = terms_of[person]
                first = min(year for _, _, year in terms)
                survivor = survivors.get(person, person)
                found = {
                    item
                    for slug in slugs
                    for item in called.get(slug, ())
                    if any(
                        office == kind
                        and where in (None, uf)
                        and start <= year + POSITIONS[office][1]
                        and end >= year + 1
                        for office, uf, year in terms
                        for kind, where, start, end in held.get(item, ())
                    )
                    and all(first - born_in >= YOUNGEST - 3 for born_in in born.get(item, ()))
                    and (not born.get(item) or not birth_years.get(survivor) or born[item] & birth_years[survivor])
                }
                if len(found) == 1:
                    found_by[found.pop()].add(survivor)
            return found_by

        everyone = candidacies.with_columns(
            final=pl.col("root").map_elements(lambda person: survivors.get(person, person), return_dtype=pl.String)
        )
        careers = []
        for person, slug, first, last, count, first_parties, last_parties in (
            everyone.join(names.select("root", "slug").unique(), on="root")
            .group_by("final", "slug")
            .agg(
                first=pl.col("year").min(),
                last=pl.col("year").max(),
                count=pl.len(),
                first_parties=pl.col("party").filter(pl.col("year") == pl.col("year").min()).drop_nulls().unique(),
                last_parties=pl.col("party").filter(pl.col("year") == pl.col("year").max()).drop_nulls().unique(),
            )
            .iter_rows()
        ):
            careers.append((person, slug, first, last, count, set(first_parties), set(last_parties)))
        slug_people = defaultdict(set)
        for person, slug, *_ in careers:
            slug_people[slug].add(person)
        careers = [career for career in careers if len(slug_people[career[1]]) > 1]
        items_of: dict[str, set[str]] = defaultdict(set)
        for item, entity in taken.items():
            items_of[survivors.get(entity, entity)].add(item)
        for item, people in claim().items():
            for person in people:
                items_of[person].add(item)
        births_of = defaultdict(set)
        for person, days in births.iter_rows():
            births_of[survivors.get(person, person)] |= {day.year for day in days}
        across = cross_state(careers, births_of, items_of)
        survivors.update(across)
        settle()
        print(f"{len(across)} persons merged across states by a shared full name")
        claims = claim()
        holders = {survivors.get(entity, entity) for entity in holding}
        linked = {
            next(iter(people)): item
            for item, people in claims.items()
            if len(people) == 1 and item not in taken and next(iter(people)) not in holders
        }
        print(
            f"{wins['root'].n_unique()} early winners, {len(claims)} Wikidata items found, "
            f"{sum(len(people) > 1 for people in claims.values())} claimed twice, "
            f"{sum(item in taken for item in claims)} already held, {len(linked)} linked"
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Candidates of 1945 to 1990 merged with candidates of 1994 and later who share their full name "
                "in the same state, were 21 to 75 at the earlier person's first election, and are the only such "
                "namesake, when they also share a party or a ballot name, or ran again by 1994, or both won; "
                "presidential and vice-presidential candidates of 1945 to 1989 merged with the state candidates "
                "who share their full name; candidates who share a full name that no other candidate of a plausible "
                "age bears, and whose careers in different states follow one another, merged when a Wikidata item "
                "or a party links them; "
                "winners of 1945 to 1990 linked to the Wikidata item whose label or alias is their full or ballot "
                "name and that held the same office for the same state in that term"
            ),
            polity="BR",
            kind="match",
        )
        for person, survivor in survivors.items():
            cs.merge(UUID(person), UUID(survivor))
        if linked:
            source = cs.source("Wikidata", TITLE)
            cs.add(
                "source",
                id=source,
                kind="wikidata",
                publisher="Wikidata",
                title=TITLE,
                url="https://www.wikidata.org/wiki/Property:P39",
                license="CC0 1.0",
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            )
            for top, _ in POSITIONS.values():
                for query in (HELD, LABELS):
                    cs.artifact(raw.cached(SOURCE, _url(query, top)), source)
            for person, item in sorted(linked.items()):
                cs.add("external_id", entity_id=UUID(person), scheme="wikidata", value=item)
        return cs.write()
