from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "junior", "senior"}


def emit(
    *,
    pipeline: str,
    version: str,
    summary: str,
    rows: pl.DataFrame,
    fips: str,
    place: str,
    publisher: str,
    title: str,
    listing: str,
    method: str,
    state_code: str = "CA",
    names_only: bool = False,
) -> Path:
    days = sorted(set(rows["day"]))
    with connect() as conn:
        cs = Changeset(conn, pipeline=pipeline, version=version, summary=summary, polity=f"US-{state_code}")
        cs.scope_units("us.precinct", f"%:{state_code}:{fips}:%")
        county = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [fips]
        ).fetchone()
        if county is None:
            raise ValueError(f"{place} has no unit")
        state = cs.polity(f"US-{state_code}")
        contests = conn.execute(
            """select distinct on (k.id) k.id, k.election_id, e.date_from, rs.stage, rs.kind,
                      (select min(v.count_round) from result_value v where v.result_set_k = rs.k and v.measure = 1)
               from contest k join election e on e.id = k.election_id
               join result_set rs on rs.contest_id = k.id and rs.is_default
               where e.date_from = any(%s) and exists (
                 select 1 from slug s where s.entity_id = e.polity_id and (s.path = %s or s.path like %s))
                 and not exists (
                   select 1 from result_set r2 join result_value v on v.result_set_k = r2.k
                   join reporting_unit u on u.k = v.unit_k
                   where r2.contest_id = k.id and u.scheme_code = 'us.precinct')""",
            [days, f"us/{state_code.lower()}", f"us/{state_code.lower()}/%%"],
        ).fetchall()
        ids = [contest for contest, *_ in contests]
        notes = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'methodology'""",
                [pipeline],
            ).fetchall()
        )
        options: dict[str, list[tuple[str, str, set[str], int]]] = {}
        for contest, option, okind, label, name, official in conn.execute(
            """with first as (
                 select rs.contest_id, rs.k, min(v.count_round) as round from result_set rs
                 join result_value v on v.result_set_k = rs.k and v.measure = 1
                 where rs.contest_id = any(%s) and rs.is_default group by 1, 2
               )
               select o.contest_id, o.id, o.kind, o.ballot_label, n.text,
                      (select sum(v.value)::bigint from result_value v
                       where v.result_set_k = f.k and v.option_k = o.k and v.measure = 1 and v.count_round = f.round)
               from contest_option o join first f on f.contest_id = o.contest_id
               left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               left join name n on n.entity_id = cy.person_id and n.is_preferred
               where o.parent_option_id is null""",
            [ids],
        ).fetchall():
            tokens = [slug(text).split("-") for text in (label, name) if text]
            texts = {word for words in tokens for word in words} | {
                a + b for words in tokens for a, b in pairwise(words)
            }
            options.setdefault(str(contest), []).append((str(option), okind, texts, int(official or 0)))
        by_day: dict[date, list[tuple[object, ...]]] = {}
        for entry in contests:
            by_day.setdefault(entry[2], []).append(entry)

        source = cs.source(publisher, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=listing,
                license="Public record",
            )
        for url in sorted(set(rows["url"])):
            cs.artifact(raw.cached(pipeline, url), source)

        report: dict[str, int] = {}
        taken: set[object] = set()
        values: list[pl.DataFrame] = []
        minted: dict[str, date] = {}
        for (day, url, _), frame in rows.group_by("day", "url", "contest", maintain_order=True):
            frame = frame.group_by("precinct", "candidate", maintain_order=True).agg(
                pl.col("votes").sum(), pl.col("registered").sum(), pl.col("ballots").sum()
            )
            sums = frame.group_by("candidate", maintain_order=True).agg(pl.col("votes").sum())
            sums = sums.filter(pl.col("votes") > 0)
            if sums.height == 0:
                report["no votes"] = report.get("no votes", 0) + 1
                continue
            winners = []
            for contest, election, _, stage, kind, first in by_day.get(day, []):
                if contest in taken or first is None:
                    continue
                known = [
                    entry
                    for entry in options.get(str(contest), [])
                    if entry[1] != "write_in" and (entry[3] > 0 or names_only)
                ]
                if len(known) != sums.height:
                    continue
                picked: dict[str, tuple[str, int]] = {}
                for candidate, votes in sums.iter_rows():
                    words = [word for word in slug(candidate).split("-") if word and word not in SUFFIXES]
                    scored = [
                        option
                        for option, okind, texts, official in known
                        if option not in {chosen for chosen, _ in picked.values()}
                        and (official * 0.97 <= votes <= official * 1.0005 + 2 or (names_only and official == 0))
                        and (
                            (okind == "answer" and candidate.upper() in ("YES", "NO") and words[0] in texts)
                            or (
                                okind != "answer"
                                and words
                                and words[-1] in texts
                                and len(set(words) & texts) >= min(2, len(words))
                            )
                        )
                    ]
                    if len(scored) == 1:
                        picked[candidate] = (scored[0], votes)
                if len(picked) == sums.height:
                    winners.append((contest, election, stage, kind, first, picked))
            if len(winners) != 1:
                report["no single matching contest" if not winners else "ambiguous"] = (
                    report.get("no single matching contest" if not winners else "ambiguous", 0) + 1
                )
                continue
            contest, election, stage, kind, first, picked = winners[0]
            taken.add(contest)
            official = sum(entry[3] for entry in options[str(contest)] if entry[1] != "write_in")
            report["matched"] = report.get("matched", 0) + 1
            result_set = cs.key(f"{pipeline}.result_set", str(contest))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind=kind,
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(sum(votes for _, votes in picked.values()) / official, 4) if official else None,
            )
            codes = {
                precinct: f"{day}:{state_code}:{fips}:{place}:{precinct}"
                for precinct in frame["precinct"].unique().to_list()
            }
            unit_ids = {}
            for precinct, code in codes.items():
                unit_ids[precinct] = cs.unit("us.precinct", code)
                if unit_ids[precinct] in cs.minted and code not in minted:
                    minted[code] = day
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[precinct],
                        polity_id=state,
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=county[0],
                        area_relation="within",
                        area_id=county[1],
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            options_by_candidate = {candidate: option for candidate, (option, _) in picked.items()}
            votes = frame.filter(pl.col("candidate").is_in(list(options_by_candidate))).select(
                result_set_id=pl.lit(str(result_set)),
                unit_id=pl.col("precinct").replace_strict({k: str(v) for k, v in unit_ids.items()}),
                option_id=pl.col("candidate").replace_strict(options_by_candidate),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(first, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value=pl.col("votes"),
            )
            accounting = (
                frame.select("precinct", "registered", "ballots")
                .unique("precinct")
                .unpivot(index="precinct", on=["registered", "ballots"], variable_name="name", value_name="value")
                .drop_nulls("value")
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("precinct").replace_strict({k: str(v) for k, v in unit_ids.items()}),
                    option_id=pl.lit(None, pl.String),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.when(pl.col("name") == "registered").then(2).otherwise(3).cast(pl.Int16),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("value"),
                )
            )
            values.extend([votes, accounting])
            cs.add(
                "note",
                id=notes.get(result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=method,
                valid_from_precision="day",
                valid_to_precision="day",
            )
        print(report)
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
