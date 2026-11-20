import csv
import io
from collections import defaultdict
from datetime import date, datetime
from itertools import combinations, pairwise
from pathlib import Path
from uuid import UUID

import polars as pl

from ea.changeset import Changeset
from ea.sources.identity import words
from ea.sources.us.wikidata_links import NICKNAMES
from ea.store import Connection, connect

SOURCE = "crosswalk"
VERSION = "3"
NEAR = 12 * 366
RIVAL = 60
MOST = 200_000
LONG_GAP = 24 * 365
LONG_SPAN = 50 * 365
MATCHED = ("tr", "br")
COUNTRIES = ("FR", "ES", "FI", "IT", "PL", "CA", "LU", "IE")
IDENTITIES = (
    "bioguide",
    "wikidata",
    "mnis",
    "ep.mep",
    "lop.person",
    "fec.candidate",
    "us.klarner.person",
    "us.alged.person",
    "us.elstats_local.person",
)
STRONG = ("bioguide", "wikidata", "mnis", "ep.mep", "lop.person", "fec.candidate")
SAME = Path(__file__).with_name("crosswalk_same.tsv")
APART = Path(__file__).with_name("crosswalk_apart.tsv")
CANON = {"rudy": "rudolph", "rudi": "rudolph"} | {
    form: first
    for first, forms in NICKNAMES.items()
    for form in forms.split()
    if form not in {"kathleen", "jon", "bert"}
}
CODED = ("person", "polity", "area", "office", "body", "party", "pipeline", "nation")
COLUMNS = (
    "person",
    "name",
    "day",
    "wide",
    "inside",
    "polity",
    "area",
    "office",
    "body",
    "party",
    "pipeline",
    "nation",
    "birth",
)
ROWS = """copy (
  with recursive up(id, at, depth) as (
    select id, id, 0 from polity
    union all
    select up.id, pp.parent_id, up.depth + 1 from up join polity_parent pp on pp.polity_id = up.at where up.depth < 8
  ), country as (
    select distinct on (up.id) up.id, split_part(p.iso_code, '-', 1) as iso, up.at as nation
    from up join polity p on p.id = up.at where p.kind = 'sovereign_state' and p.iso_code is not null
    order by up.id, up.depth
  ), inside as (select id from country where iso = any(%(countries)s))
  select cy.person_id, coalesce(cy.ballot_name, n.text), e.date_from,
         (p.kind in ('sovereign_state', 'federal_unit', 'region'))::int, (e.polity_id in (select id from inside))::int,
         e.polity_id, k.district_area_id, k.office_id, k.body_id, o.party_id, c.pipeline,
         n2.nation,
         case when x.birth_precision = 'day' then x.birth_date end
  from candidacy cy
  join contest_option o on o.id = cy.option_id
  join contest k on k.id = o.contest_id
  join election e on e.id = k.election_id
  join polity p on p.id = e.polity_id
  join changeset c on c.id = cy.changeset_id
  join person x on x.id = cy.person_id
  left join country n2 on n2.id = e.polity_id
  left join lateral (select text from name where entity_id = cy.person_id and is_preferred limit 1) n on true
  where e.date_from is not null and split_part(c.pipeline, '.', 1) <> all(%(matched)s)
    and cy.person_id in (
      select y.person_id from candidacy y
      join contest_option yo on yo.id = y.option_id
      join contest yk on yk.id = yo.contest_id
      join election ye on ye.id = yk.election_id
      where ye.polity_id in (select id from inside)
      union select id from person where birth_precision = 'day')
) to stdout with (format csv)"""
EPOCH = date(1970, 1, 1).toordinal()
SOLE = """select k.office_id::text from contest k join office f on f.id = k.office_id
          join entity e on e.id = k.id and e.retired_by is null
          where k.district_area_id is null and f.kind <> 'head_of_state'
          group by k.office_id having count(*) = count(distinct (k.election_id, k.party_scope_id))"""


def _frame(conn: Connection) -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    buffer = io.BytesIO()
    with conn.cursor() as cursor, cursor.copy(ROWS, {"countries": list(COUNTRIES), "matched": list(MATCHED)}) as copy:
        for chunk in copy:
            buffer.write(chunk)
    frame = pl.read_csv(
        buffer.getvalue(),
        has_header=False,
        new_columns=list(COLUMNS),
        schema_overrides={
            column: pl.Date if column in ("day", "birth") else pl.Int8 if column in ("wide", "inside") else pl.String
            for column in COLUMNS
        },
    ).with_columns(
        pl.col("day").cast(pl.Int32),
        pl.col("birth").cast(pl.Int32),
        pl.col("wide").cast(pl.Boolean),
        pl.col("inside").cast(pl.Boolean),
    )
    del buffer
    named = pl.DataFrame(
        [
            (text, f"{CANON.get(first, first)} {rest}", first, "".join(sorted(folded[1])))
            for text in frame["name"].drop_nulls().unique().to_list()
            if (folded := words(text)) is not None
            for first, rest in [folded[0].split(" ", 1)]
        ],
        schema={"name": pl.String, "key": pl.String, "first": pl.String, "initials": pl.String},
        orient="row",
    )
    frame = frame.join(named, on="name", how="left").drop("name")
    codes: dict[str, pl.DataFrame] = {}
    for column in CODED:
        codes[column] = frame.select(pl.col(column).drop_nulls().unique()).with_row_index("code")
        frame = frame.join(codes[column], on=column, how="left").drop(column).rename({"code": column})
    return frame, codes


def _seats(frame: pl.DataFrame, sole: list[int]) -> pl.DataFrame:
    line = ["seat", "office", "body"]
    lined = (
        frame.filter(
            pl.col("inside")
            & pl.col("key").is_not_null()
            & (pl.col("office").is_not_null() | pl.col("body").is_not_null())
        )
        .with_columns(
            seat=pl.when(pl.col("area").is_not_null())
            .then(pl.col("area").cast(pl.Int64))
            .when(pl.col("office").is_in(sole))
            .then(-1 - pl.col("office").cast(pl.Int64)),
            rest=pl.col("key").str.split_exact(" ", 1).struct.field("field_1"),
            lead=pl.col("key").str.slice(0, 1),
        )
        .filter(pl.col("seat").is_not_null())
    )
    spelled = (
        lined.filter(pl.col("first").str.len_chars() > 1)
        .group_by([*line, "rest", "lead"])
        .agg(pl.col("key").n_unique().alias("keys"), pl.col("key").first(), pl.col("person").n_unique().alias("people"))
        .filter((pl.col("keys") == 1) & (pl.col("people") == 1))
        .select([*line, "rest", "lead", pl.col("key").alias("spelled")])
    )
    return (
        lined.join(spelled, on=[*line, "rest", "lead"], how="left", nulls_equal=True)
        .with_columns(
            key=pl.when(pl.col("first").str.len_chars() == 1).then(pl.coalesce("spelled", "key")).otherwise("key")
        )
        .group_by([*line, "key"])
        .agg(pl.col("person").unique())
        .filter(pl.col("person").list.len() > 1)
    )


def _bodies(frame: pl.DataFrame) -> pl.DataFrame:
    named = frame.filter(pl.col("key").is_not_null())
    bearers = named.group_by(["polity", "key"]).agg(pl.col("person").n_unique().alias("bearers"))
    return (
        named.filter(pl.col("body").is_not_null() & pl.col("wide") & pl.col("inside"))
        .group_by(["polity", "body", "key"])
        .agg(pl.col("person").unique())
        .join(bearers, on=["polity", "key"])
        .filter((pl.col("person").list.len() > 1) & (pl.col("person").list.len() == pl.col("bearers")))
        .drop("bearers")
    )


def _births(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame.filter(pl.col("key").is_not_null() & pl.col("birth").is_not_null() & pl.col("nation").is_not_null())
        .group_by(["nation", "key", "birth"])
        .agg(pl.col("person").unique())
        .filter(pl.col("person").list.len() > 1)
    )


def emit() -> Path:
    with connect() as conn:
        conn.execute("set statement_timeout = 0")
        busy = [
            pipeline
            for (pipeline,) in conn.execute(
                "select distinct pipeline from changeset where status in ('draft', 'pending_review')"
            ).fetchall()
        ]
        sole = [office for (office,) in conn.execute(SOLE).fetchall()]
        frame, codes = _frame(conn)

        def coded(column: str, values: list[str]) -> list[int]:
            return codes[column].filter(pl.col(column).is_in(values))["code"].to_list()

        seats = _seats(frame, coded("office", sole))
        bodies = _bodies(frame)
        births = _births(frame)
        people = (
            set(seats["person"].explode().to_list())
            | set(bodies["person"].explode().to_list())
            | set(births["person"].explode().to_list())
        )
        print(
            f"{frame.height} candidacies, {len(people)} people in {seats.height} seat, {bodies.height} body, "
            f"and {births.height} birth groups"
        )
        rows = frame.filter(pl.col("person").is_in(list(people)))
        held_busy = set(rows.filter(pl.col("pipeline").is_in(coded("pipeline", busy)))["person"].to_list())
        del frame

        dates: dict[int, set[int]] = defaultdict(set)
        held: dict[int, set[tuple[int, int | None, int | None, int | None]]] = defaultdict(set)
        given: dict[int, set[str]] = defaultdict(set)
        initials: dict[int, set[str]] = defaultdict(set)
        stood: dict[tuple[int, int, int], list[tuple[int, int | None]]] = defaultdict(list)
        for person, day, polity, area, office, body, party, first, signed in rows.select(
            "person", "day", "polity", "area", "office", "body", "party", "first", "initials"
        ).iter_rows():
            dates[person].add(day)
            held[person].add((day, area, office, body))
            if first is not None:
                given[person].add(first)
            if signed:
                initials[person].add(signed)
            if body is not None:
                stood[person, polity, body].append((day, party))
        del rows

        uuid: dict[int, str] = dict(codes["person"].filter(pl.col("code").is_in(list(people))).iter_rows())
        person_of = {text: code for code, text in uuid.items()}
        conn.execute("create temp table crosswalk_people (id uuid primary key) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy crosswalk_people (id) from stdin") as copy:
            for text in uuid.values():
                copy.write_row((text,))
        created: dict[int, datetime] = {
            person_of[str(person)]: when
            for person, when in conn.execute(
                "select e.id, e.created_at from entity e join crosswalk_people p on p.id = e.id"
            ).fetchall()
        }
        born: dict[int, date] = {}
        died: dict[int, int] = {}
        for person, birth, death in conn.execute(
            """select x.id, x.birth_date, x.death_date from person x join crosswalk_people p on p.id = x.id
               where x.birth_date is not null or x.death_date is not null"""
        ).fetchall():
            if birth is not None:
                born[person_of[str(person)]] = birth
            if death is not None:
                died[person_of[str(person)]] = death.toordinal() - EPOCH
        ids: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for person, scheme, value in conn.execute(
            """select x.entity_id, x.scheme, x.value from external_id x join crosswalk_people p on p.id = x.entity_id
               where x.scheme = any(%s)
                  or x.scheme = 'us.nnv.person' and x.value ~ '^[A-Z]{2}[0-9]{4}' and x.value !~ '^AA0000'""",
            [list(IDENTITIES)],
        ).fetchall():
            ids[person_of[str(person)]][scheme].add(value)
        with APART.open(newline="", encoding="utf-8") as handle:
            different = {
                frozenset((person_of[entry["one"]], person_of[entry["two"]]))
                for entry in csv.DictReader(handle, delimiter="\t")
                if entry["one"] in person_of and entry["two"] in person_of
            }

        def suspect(person: int) -> bool:
            days = sorted(dates[person])
            return (
                person in held_busy
                or days[-1] - days[0] > LONG_SPAN
                or max((later - earlier for earlier, later in pairwise(days)), default=0) > LONG_GAP
            )

        def kin(one: str, two: str) -> bool:
            return (
                one == two
                or CANON.get(one, one) == CANON.get(two, two)
                or (len(one) == 1 or len(two) == 1)
                and one[0] == two[0]
                or one.startswith(two)
                or two.startswith(one)
            )

        def apart(one: int, two: int) -> str | None:
            if frozenset((one, two)) in different:
                return "listed as different people"
            if suspect(one) or suspect(two):
                return "busy or long career"
            if dates[one] & dates[two]:
                return "stood on the same day"
            if any(abs(a[0] - b[0]) <= RIVAL and a[1:] != b[1:] for a in held[one] for b in held[two]):
                return "stood for another seat within 60 days"
            if not all(kin(a, b) for a in given[one] for b in given[two]):
                return "first names differ"
            if len(initials[one] | initials[two]) > 1:
                return "middle initials differ"
            if one in born and two in born and born[one] != born[two]:
                return "birth dates differ"
            if any(
                first in died and max(dates[second]) - died[first] > 365 for first, second in ((one, two), (two, one))
            ):
                return "stood after death"
            return None

        decode = {
            column: dict(codes[column].select("code", column).iter_rows())
            for column in ("polity", "area", "office", "body", "party", "nation")
        }

        def day_of(day: int) -> date:
            return date.fromordinal(EPOCH + day)

        parent: dict[int, int] = {}

        def root(node: int) -> int:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        edges: list[tuple[int, int, str, str, str, str, str | None, date, date]] = []
        refused: defaultdict[str, int] = defaultdict(int)
        for seat, office, body, key, members in seats.iter_rows():
            for one, two in combinations(sorted(members), 2):
                if (reason := apart(one, two)) is not None:
                    refused[f"seat: {reason}"] += 1
                    continue
                near, day, other = min((abs(a - b), a, b) for a in dates[one] for b in dates[two])
                if near > NEAR:
                    refused["seat: more than 12 years apart"] += 1
                    continue
                parent[root(one)] = root(two)
                edges.append(
                    (
                        one,
                        two,
                        "seat",
                        key,
                        decode["area"][seat] if seat >= 0 else decode["office"][-1 - seat],
                        decode["office"][office] if body is None else decode["body"][body],
                        None,
                        day_of(day),
                        day_of(other),
                    )
                )
        for polity, body, key, members in bodies.iter_rows():
            if (reason := next(filter(None, (apart(a, b) for a, b in combinations(members, 2))), None)) is not None:
                refused[f"body: {reason}"] += 1
                continue
            ordered = sorted(members, key=lambda person: min(day for day, _ in stood[person, polity, body]))
            chain = []
            for one, two in pairwise(ordered):
                mine, theirs = min(
                    ((a, b) for a in stood[one, polity, body] for b in stood[two, polity, body]),
                    key=lambda pair: (abs(pair[0][0] - pair[1][0]), pair[0][1] != pair[1][1]),
                )
                if abs(mine[0] - theirs[0]) > NEAR or mine[1] is None or mine[1] != theirs[1]:
                    chain = []
                    refused["body: no shared party within 12 years"] += 1
                    break
                chain.append(
                    (
                        one,
                        two,
                        "body",
                        key,
                        decode["polity"][polity],
                        decode["body"][body],
                        decode["party"][mine[1]],
                        day_of(mine[0]),
                        day_of(theirs[0]),
                    )
                )
            for edge in chain:
                parent[root(edge[0])] = root(edge[1])
                edges.append(edge)

        for nation, key, birth, members in births.iter_rows():
            for one, two in combinations(sorted(members), 2):
                if (reason := apart(one, two)) is not None:
                    refused[f"birth: {reason}"] += 1
                    continue
                _, day, other = min((abs(a - b), a, b) for a in dates[one] for b in dates[two])
                parent[root(one)] = root(two)
                edges.append(
                    (
                        one,
                        two,
                        "birth",
                        key,
                        decode["nation"][nation],
                        day_of(birth).isoformat(),
                        None,
                        day_of(day),
                        day_of(other),
                    )
                )

        groups: dict[int, list[int]] = defaultdict(list)
        for person in list(parent):
            groups[root(person)].append(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "People in France, Spain, Finland, Italy, Poland, Canada, Luxembourg, and Ireland recorded once per "
                "source or per race joined across elections, where the same printed name "
                "ran for the same seat within 12 years, or for the same legislature of a country, state, or region under "
                "the same party within 12 years with no one else of that name there, and nothing tells them apart; "
                "people anywhere but Brazil and Turkey who share a printed name, a country, and an exact birth date, "
                "and nothing tells them apart; plus pairs joined by hand with cited evidence"
            ),
            polity=None,
            kind="match",
        )
        rules: dict[int, set[str]] = defaultdict(set)
        for edge in edges:
            rules[root(edge[0])].add(edge[2])
        outcome: dict[int, str] = {}
        merges: list[tuple[str, str, str]] = []
        for group, members in sorted(groups.items(), key=lambda item: uuid[item[0]]):
            if len(merges) >= MOST:
                outcome[group] = "next run"
                continue
            reason = next(filter(None, (apart(a, b) for a, b in combinations(members, 2))), None)
            if reason is None and any(
                len({value for person in members for value in ids[person].get(scheme, ())}) > 1
                for scheme in {scheme for person in members for scheme in ids[person]}
            ):
                reason = "conflicting identifiers"
            if reason is not None:
                refused[f"group: {reason}"] += 1
                outcome[group] = f"refused: {reason}"
                continue
            survivor = max(
                members,
                key=lambda person: (
                    any(scheme in ids[person] for scheme in STRONG),
                    len(dates[person]),
                    -created[person].timestamp(),
                    -UUID(uuid[person]).int,
                ),
            )
            outcome[group] = "merged"
            for person in members:
                if person != survivor:
                    cs.merge(UUID(uuid[person]), UUID(uuid[survivor]))
                    merges.append((uuid[person], uuid[survivor], f"{' and '.join(sorted(rules[group]))} rule"))

        with SAME.open(newline="", encoding="utf-8") as handle:
            for entry in csv.DictReader(handle, delimiter="\t"):
                duplicate, survivor = UUID(entry["duplicate"]), UUID(entry["survivor"])
                if cs.survivors.get(duplicate, duplicate) == cs.survivors.get(survivor, survivor):
                    print(f"{entry['name']}: already one person")
                    continue
                cs.merge(duplicate, survivor)
                merges.append((str(duplicate), str(cs.survivors.get(survivor, survivor)), entry["evidence"]))
        print(f"{len(merges)} merges; refused: {dict(sorted(refused.items()))}")
        folder = cs.write()

        evidence = pl.DataFrame(
            [
                (outcome[root(one)], uuid[one], uuid[two], *rest)
                for one, two, *rest in edges
                if outcome[root(one)] != "next run"
            ],
            schema={
                **dict.fromkeys(("outcome", "one", "two", "rule", "key", "place", "seat", "party"), pl.String),
                "one_day": pl.Date,
                "two_day": pl.Date,
            },
            orient="row",
        )
        named = [
            *evidence["one"],
            *evidence["two"],
            *evidence["place"],
            *evidence.filter(pl.col("rule") != "birth")["seat"],
            *evidence["party"],
        ]
        labels = pl.DataFrame(
            conn.execute(
                """select distinct on (entity_id) entity_id::text, text from name
                   where is_preferred and entity_id = any(%s) order by entity_id, lang <> 'en'""",
                [[UUID(value) for value in {value for value in named if value is not None}]],
            ).fetchall(),
            schema=["id", "label"],
            orient="row",
        )
        for column in ("one", "two", "place", "seat", "party"):
            evidence = evidence.join(labels.rename({"id": column, "label": f"{column}_name"}), on=column, how="left")
        evidence.write_csv(folder / "edges.tsv.gz", separator="\t", compression="gzip", check_extension=False)
        pl.DataFrame(merges, schema=["duplicate", "survivor", "evidence"], orient="row").write_csv(
            folder / "merges.tsv.gz", separator="\t", compression="gzip", check_extension=False
        )
        return folder
