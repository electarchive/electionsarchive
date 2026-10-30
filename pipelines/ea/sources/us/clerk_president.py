import itertools
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import clerk
from ea.store import connect

SOURCE = "us.clerk_president"
VERSION = "1"
RELIABLE = {"agreed", "confirmed", "settled"}
DEMOTED = {
    (1920, "FL"),
    (1928, "ID"),
    (1940, "TX"),
}
TIED = {(1956, "GA"), (1960, "WY"), (1964, "NH"), (1968, "CA"), (1972, "FL"), (1976, "VT")}
CORRECTED = {(1972, "FL", "Democratic"): 718117}
FIXED = {
    (1944, "FL", 148215): 143215,
    (1944, "IA", 8752): 3752,
    (1964, "IA", 773030): 733030,
    (1968, "MI", 1598082): 1593082,
    (1972, "AZ", 80945): 30945,
}
VERIFIED = {
    (1920, "MT"),
    (1920, "PA"),
    (1924, "MI"),
    (1932, "SD"),
    (1944, "FL"),
    (1944, "GA"),
    (1944, "IA"),
    (1944, "KY"),
    (1948, "PA"),
    (1948, "WA"),
    (1960, "TN"),
    (1960, "WA"),
    (1964, "IA"),
    (1968, "AZ"),
    (1968, "GA"),
    (1968, "MI"),
    (1972, "AZ"),
    (1972, "CA"),
    (1972, "OH"),
    (1972, "SC"),
    (1976, "ID"),
    (1976, "MS"),
    (1976, "VA"),
    (1980, "IN"),
    (1980, "LA"),
    (1980, "MI"),
    (1980, "NE"),
}
UNEQUAL = {
    (1924, "NY", "robertlafollette"): ("Independent Progressive", "Socialist (La Follette)"),
}
ALIASES = {
    "democrat": "democratic",
    "prohibitionist": "prohibition",
    "socialistlabour": "socialistlabor",
    "socialistworker": "socialistworkers",
    "socialistworkers": "socialistworkers",
    "statesrightsdemocrats": "statesrightsdemocratic",
    "statesrights": "statesrightsdemocratic",
    "nationalstatesrights": "statesrightsdemocratic",
}
NOTE = (
    "The state's official vote for presidential electors as printed in the Clerk of the House's biennial Statistics of "
    "the Presidential and Congressional Election. The book prints each party's electors under the party's name, so "
    "tickets that Wikipedia's results-by-state table names keep that table's candidates, and every other party is a "
    "legend line without a candidate. Write-ins and scattering votes are pooled as Others. Two readings of the page by "
    "a vision model and one reading of each number by cell OCR were reconciled, and each digitized number carries "
    "its page and box. Wikipedia's earlier figures are kept as a superseded, non-default set."
)


def norm(text: str | None) -> str:
    text = re.sub(r"(?i)\b(party|electors?|presidential|the)\b", "", text or "").lower()
    text = re.sub(r"[^a-z]", "", text)
    return ALIASES.get(text, text)


def line_names(label: str) -> set[str]:
    inner = re.findall(r"\(([^()]*)\)", label)
    head = re.sub(r"\(.*?\)", "", label).split(":")[0]
    return {norm(head), *(norm(part) for text in inner for part in text.split("/"))} - {"", "writein"}


def emit() -> Path:
    lines = (
        pl.read_parquet(clerk.PARSED / "lines.parquet")
        .filter(pl.col("office") == "electors", pl.col("state").is_not_null())
        .with_columns(
            label=pl.when(pl.col("name").is_null() | pl.col("name").str.contains(r"(?i)^presidential electors?$"))
            .then(pl.col("party"))
            .otherwise(pl.col("name"))
        )
    )
    days = {year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in clerk.YEARS}
    skipped: list[str] = []
    differences: list[str] = []
    with connect() as conn:
        old: dict[tuple[int, str], tuple] = {}
        for row in conn.execute(
            """select extract(year from e.date_from)::int, upper(split_part(sl.path, '/', 2)), r.id, r.k, r.election_id,
                      r.contest_id, r.stage, r.kind, r.source_id, r.completeness, sl.path
               from result_set r join contest k on k.id = r.contest_id join election e on e.id = r.election_id
               join slug sl on sl.entity_id = k.id and sl.path like 'us/__/%%/president'
               join source s on s.id = r.source_id
               where k.kind = 'elector_slate' and e.date_from between '1920-01-01' and '1983-12-31'
                 and s.publisher = 'Wikipedia' and sl.path not like %s
                 and (r.is_default or exists (select 1 from result_set c join changeset ch on ch.id = c.changeset_id
                      where c.supersedes_id = r.id and ch.pipeline = %s))""",
            ["us/me/%", SOURCE],
        ).fetchall():
            old[(row[0], row[1])] = row[2:]
        options: dict[UUID, list[tuple]] = defaultdict(list)
        for rs, option, label, kind, party, unit, measure, value in conn.execute(
            """select r.id, o.id, o.ballot_label, o.kind, o.party_id, u.id, v.measure, v.value
               from result_set r join result_value v on v.result_set_k = r.k
               left join contest_option o on o.k = v.option_k join reporting_unit u on u.k = v.unit_k
               where r.id = any(%s) and v.channel = 0""",
            [[row[0] for row in old.values()]],
        ).fetchall():
            options[rs].append((option, label, kind, party, unit, measure, Decimal(value)))
        outcomes: dict[UUID, list[tuple]] = defaultdict(list)
        for rs, option, kind, seats, candidacy in conn.execute(
            "select result_set_id, option_id, kind, seats, candidacy_id from outcome where result_set_id = any(%s)",
            [[row[0] for row in old.values()]],
        ).fetchall():
            outcomes[rs].append((option, kind, seats, candidacy))
        parties = {
            re.sub(r"[^a-z]", "", text.lower()): party
            for text, party in conn.execute(
                """select n.text, p.id from party p join name n on n.entity_id = p.id and n.lang = 'en'
                   join polity y on y.id = p.polity_id where y.iso_code = 'US' and n.text <> 'Progressive Party'"""
            ).fetchall()
        }
        sizes = {
            (artifact, number): (width, height)
            for artifact, number, width, height in conn.execute(
                "select artifact_id, page_number, width, height from source_page"
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential results by state 1920–1980 from the Clerk's statistics, superseding Wikipedia's",
            polity="US",
        )
        artifacts = {}
        books = {}
        for year in clerk.YEARS:
            title = (
                f"Statistics of the {'Presidential and ' if year % 4 == 0 else ''}Congressional Election of "
                f"{days[year]:%B %-d, %Y}"
            )
            books[year] = cs.source(clerk.PUBLISHER, title)
            artifacts[year] = cs.artifact(raw.cached(clerk.SOURCE, clerk.BOOK.format(year=year)), books[year])
        for (year, state), group in lines.group_by(["year", "state"]):
            if (year, state) not in old:
                continue
            rs, _, election, contest, stage, kind, source, completeness, _ = old[(year, state)]
            rows = group.sort("page", "line").to_dicts()
            for row in rows:
                row["votes"] = CORRECTED.get(
                    (year, state, row["label"]), FIXED.get((year, state, row["votes"]), row["votes"])
                )
            tag = f"{year} {state}"
            if (year, state) in DEMOTED:
                cs.add(
                    "result_set",
                    id=cs.key(f"{SOURCE}.result_set", f"{year}:{state}"),
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    source_id=books[year],
                    status="withdrawn",
                    is_default=False,
                    completeness=1,
                )
                cs.add(
                    "result_set",
                    id=rs,
                    election_id=election,
                    contest_id=contest,
                    stage=stage,
                    kind=kind,
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=completeness,
                )
                continue
            tickets: dict[UUID, tuple[set[str], Decimal]] = {}
            heads: dict[UUID, str] = {}
            for option, label, okind, party, unit, measure, value in options[rs]:
                if okind == "ticket":
                    match = re.search(r"\(([^()]*)\)\s*$", label or "")
                    tickets[option] = ({norm(part) for part in (match[1] if match else label or "").split("/")}, value)
                    heads[option] = norm(re.sub(r"\(.*\)", "", label or ""))
            others = next((o for o, _, k, _, _, _, _ in options[rs] if k == "others"), None)
            unit = next(u for _, _, _, _, u, _, _ in options[rs])
            taken: list[dict] = []
            scatter_rows: list[dict] = []
            for row in rows:
                label = (row["label"] or "").strip()
                if row["votes"] is None or row["kind"] in ("total", "blank", "subtotal"):
                    continue
                if re.search(r"(?i)\b(blank|void|defective|spoiled|rejected)\b", label):
                    continue
                if row["kind"] == "scattering" or re.match(
                    r"(?i)^(write-?ins?|scattering|others?|all others|void and scattering)$", label
                ):
                    scatter_rows.append(row)
                elif not any(line["label"] == label and line["votes"] == row["votes"] for line in taken):
                    taken.append(row | {"label": label})
            if any(not norm(row["label"]) for row in taken):
                skipped.append(f"{tag}: a line with no name")
                continue
            merges: list[str] = []
            for option, (tokens, value) in tickets.items():
                if not value or any(row["votes"] == value for row in taken):
                    continue
                spare = [i for i, row in enumerate(taken) if row["votes"] not in {v for _, v in tickets.values()}]
                found = [
                    group
                    for size in (2, 3)
                    for group in itertools.combinations(spare, size)
                    if sum(taken[i]["votes"] for i in group) == value
                    and any((tokens | {heads[option]}) & line_names(taken[i]["label"]) for i in group)
                ]
                if not found and (year, state, heads[option]) in UNEQUAL:
                    found = [
                        tuple(i for i, row in enumerate(taken) if row["label"] in UNEQUAL[(year, state, heads[option])])
                    ]
                if len(found) != 1:
                    continue
                group = found[0]
                total_lines = sum(taken[i]["votes"] for i in group)
                merges.append(
                    " and ".join(f"{taken[i]['votes']:,} on the {taken[i]['label']} line" for i in group)
                    + (
                        f" (Wikipedia's figure is {value:,})"
                        if total_lines != value
                        else ""
                    )
                )
                taken[group[0]] = taken[group[0]] | {"votes": total_lines}
                taken = [row for i, row in enumerate(taken) if i not in group[1:]]
            matched: dict[int, UUID] = {}
            names = [line_names(row["label"]) for row in taken]
            for option, (tokens, value) in tickets.items():
                same = [i for i, row in enumerate(taken) if i not in matched and row["votes"] == value]
                if len(same) > 1:
                    same = [i for i in same if tokens & names[i]]
                if len(same) == 1 and (tokens & names[same[0]] or value):
                    matched[same[0]] = option
            for option, (tokens, value) in tickets.items():
                if option in matched.values():
                    continue
                hits = [i for i in range(len(taken)) if i not in matched and tokens & names[i]]
                if len(hits) == 1:
                    matched[hits[0]] = option
            free = [o for o, (_, value) in tickets.items() if value and o not in matched.values()]
            loose = [i for i in range(len(taken)) if i not in matched]
            if len(free) == 1 and len(loose) == 1:
                before = tickets[free[0]][1]
                if abs(taken[loose[0]]["votes"] - before) <= before / 10:
                    matched[loose[0]] = free.pop()
            winning = {option for option, *_ in outcomes[rs]}
            total_before = next(v for _, _, _, _, _, m, v in options[rs] if m == 4)
            free = [o for o in free if o in winning or tickets[o][1] * 100 > total_before]
            fused = [
                index
                for index, option in matched.items()
                if tickets[option][1] != taken[index]["votes"]
                and any(
                    taken[index]["votes"] + sum(taken[i]["votes"] for i in extra) == tickets[option][1]
                    for size in (1, 2)
                    for extra in itertools.combinations([i for i in range(len(taken)) if i not in matched], size)
                )
            ]
            if fused:
                skipped.append(f"{tag}: fusion, Wikipedia's ticket sums lines the book prints apart")
                continue
            if free:
                skipped.append(f"{tag}: Wikipedia lines not in the book: {[(tickets[o][1]) for o in free]}")
                continue
            problem = next(
                (
                    f"{row['label']} {row['votes']} is {row['status']} and is not Wikipedia's figure"
                    for index, row in enumerate(taken)
                    if row["status"] not in RELIABLE
                    and (year, state) not in VERIFIED
                    and (index not in matched or tickets[matched[index]][1] != row["votes"])
                ),
                None,
            ) or next(
                (
                    f"scattering {row['votes']} is {row['status']}"
                    for row in scatter_rows
                    if row["status"] not in RELIABLE and (year, state) not in VERIFIED
                ),
                None,
            )
            if problem:
                skipped.append(f"{tag}: {problem}")
                continue
            scatter = sum(row["votes"] for row in scatter_rows)
            winners = outcomes[rs]
            if any(option not in matched.values() for option, *_ in winners):
                skipped.append(f"{tag}: a winning option is not in the book's lines")
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{year}:{state}")
            ids: dict[int, UUID] = dict(matched)
            seen: Counter[str] = Counter()
            for index, row in enumerate(taken):
                if index in ids:
                    continue
                seen[slug(row["label"])] += 1
                ids[index] = cs.key(
                    f"{SOURCE}.option", f"{year}:{state}:{slug(row['label'])}:{seen[slug(row['label'])]}"
                )
                party = next(
                    (
                        parties[candidate]
                        for candidate in (
                            re.sub(r"[^a-z]", "", f"{row['label']} party".lower()),
                            re.sub(r"[^a-z]", "", row["label"].lower()),
                        )
                        if candidate in parties
                    ),
                    None,
                )
                cs.add(
                    "contest_option",
                    id=ids[index],
                    contest_id=contest,
                    kind="legend",
                    party_id=party,
                    ballot_label=row["label"],
                )
            if scatter and others is None:
                others = cs.key(f"{SOURCE}.option", f"{year}:{state}:others")
                cs.add("contest_option", id=others, contest_id=contest, kind="others", ballot_label="Others")
            cs.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind=kind,
                source_id=source,
                status="superseded",
                is_default=False,
                completeness=completeness,
            )
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                source_id=cs.source(
                    clerk.PUBLISHER,
                    f"Statistics of the Presidential and Congressional Election of {days[year]:%B %-d, %Y}",
                ),
                status="active",
                is_default=True,
                supersedes_id=rs,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            if merges:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="The book prints fusion votes on separate lines. A ticket's total here is the sum of "
                    + "; ".join(merges)
                    + ", which equals the figure in Wikipedia's results-by-state table.",
                )
            if (year, state) in TIED:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=(
                        "The book's own recapitulation table prints a state total that equals the sum of these lines, "
                        "so the set is kept although a top candidate differs from Wikipedia's figure by more than 1%."
                        + (
                            " The headline line prints the Democratic vote as 2,718,117; the recapitulation prints "
                            "718,117 and its total of 2,583,283 requires that figure, so 718,117 is stored."
                            if (year, state) == (1972, "FL")
                            else ""
                        )
                    ),
                )
            cells: dict[UUID, int] = {ids[index]: row["votes"] for index, row in enumerate(taken)}
            if scatter:
                cells[others] = scatter
            total = sum(cells.values())
            for option, label, okind, party, u, measure, value in options[rs]:
                if measure == 4 and value and abs(total - value) / value > Decimal("0.01"):
                    differences.append(f"{tag}: total {total} against Wikipedia's {value}")
            for index, row in enumerate(taken):
                before = tickets[matched[index]][1] if index in matched else None
                if before and abs(row["votes"] - before) / before > Decimal("0.01"):
                    differences.append(f"{tag}: {row['label']} {row['votes']} against Wikipedia's {before}")
            for option, votes in [*cells.items(), (None, total)]:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1 if option else 4,
                    count_round=0,
                    flags=clerk.DIGITIZED if option else 0,
                    value=votes,
                )
            for index, row in enumerate(taken):
                if row["box"] is None:
                    continue
                width, height = sizes[(artifacts[year], row["page"])]
                x0, y0, x1, y1 = row["box"]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=ids[index],
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=cs.page(artifacts[year], int(row["page"])),
                    source_id=None,
                    x0=x0 / width / 3,
                    y0=y0 / height / 3,
                    x1=x1 / width / 3,
                    y1=y1 / height / 3,
                )
            for option, okind, seats, candidacy in winners:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=okind,
                    seats=seats,
                    candidacy_id=candidacy,
                )
        for line in sorted(skipped):
            print("skipped", line)
        for line in sorted(differences):
            print("differs", line)
        print(f"{len(skipped)} states skipped, {len(differences)} differences over 1%")
        return cs.write()
