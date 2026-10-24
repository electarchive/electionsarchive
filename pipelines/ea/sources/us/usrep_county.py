from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.raw import Artifact
from ea.store import connect


def emit(
    source_name: str,
    version: str,
    summary: str,
    publisher: str,
    title: str,
    url: str,
    state: str,
    prefix: str,
    frame: pl.DataFrame,
    artifacts: Iterable[Artifact],
    note_for: Callable[[str], str],
    official: bool = False,
    statewide: bool = False,
) -> Path:
    with connect() as conn:
        cs = Changeset(conn, pipeline=source_name, version=version, summary=summary, polity="US")
        source = cs.source(publisher, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public record",
            )
        for artifact in artifacts:
            cs.artifact(artifact, source)
        units = dict(
            conn.execute(
                f"select code, id from reporting_unit where scheme_code = 'us.county' and code like '{prefix}%%'"
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                f"select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '{prefix}%%'"
            ).fetchall()
        )
        split = {
            (day, county)
            for (day, county), districts in frame.group_by("day", "county")
            if districts["district"].n_unique() > 1
        }
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ %s",
                [f":{state}:" + ("(president|governor|senate)$" if statewide else "house:")],
            )
        )
        for (day, district), rows in frame.group_by("day", "district", maintain_order=True):
            key = f"{day}:{state}:{district}" if statewide else f"{day}:{state}:house:{district}"
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest in the archive")
                continue
            old = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            if len(old) != 1:
                print(f"{key}: {len(old)} default result sets")
                continue
            ((county_rows,),) = conn.execute(
                """select count(*) from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k
                   where r.id = %s and u.scheme_code in ('us.county', 'us.county_part')""",
                [old[0][0]],
            ).fetchall()
            if county_rows:
                continue
            row = old[0]
            held = dict(
                conn.execute(
                    """select o.id, sum(v.value) from result_value v join result_set r on r.k = v.result_set_k
                       join contest_option o on o.k = v.option_k
                       where r.id = %s and v.measure = 1 group by o.id""",
                    [row[0]],
                ).fetchall()
            )
            sums = {column: int(g["votes"].sum()) for (column,), g in rows.group_by("column")}
            targets: dict[str, object] = {}
            for column, total in sums.items():
                same = [option for option, value in held.items() if value == total and option not in targets.values()]
                if len(same) == 1:
                    targets[column] = same[0]
            for column, total in sums.items():
                if column in targets:
                    continue
                near = sorted(
                    (abs(value - total), option) for option, value in held.items() if option not in targets.values()
                )
                if near and near[0][0] * 50 <= total:
                    targets[column] = near[0][1]
            missing = sorted(set(sums) - set(targets))
            if any(sums[column] * 100 > sum(sums.values()) for column in missing):
                print(f"{key}: unmatched {[(c, sums[c]) for c in missing]}; held {held}")
                continue
            if missing:
                writeins = cs.key("us.option", f"{key}:writein:nc_usrep")
                if writeins in cs.minted:
                    cs.add("contest_option", id=writeins, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                for column in missing:
                    targets[column] = writeins
            totals = {}
            for column, option in targets.items():
                totals[option] = totals.get(option, 0) + sums[column]
            ties = all(held.get(option) == value for option, value in totals.items()) and set(held) <= set(totals)
            differences = [
                f"{int(value - held.get(option, 0)):+d}"
                for option, value in totals.items()
                if held.get(option, 0) != value
            ]
            print(key, "ties" if ties else f"differs {differences}")
            result_set = cs.key(f"{source_name}.result_set", key)
            default = ties or official
            if default:
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            row,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            cs.add(
                "result_set",
                id=result_set,
                election_id=row[1],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                supersedes_id=row[0] if default else None,
                completeness=1,
            )
            note = note_for(key)
            if not ties:
                note += (
                    f" It differs from the Federal Election Commission's compilation by {', '.join(differences)} votes."
                    if differences
                    else " Every option printed here ties the default result set, which also holds an option this source does not print."
                )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            if default:
                for option, kind, seats in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [row[0]]
                ).fetchall():
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            unit_ids = dict(units)
            ((district_area,),) = conn.execute(
                "select district_area_id from contest where id = %s", [contest]
            ).fetchall()
            for county in rows["county"].unique():
                if (day, county) not in split:
                    continue
                part = f"{day[:4]}:{county}:{district}"
                unit_ids[county] = cs.unit("us.county_part", part)
                if unit_ids[county] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[county],
                        polity_id=cs.polity(f"US-{state}"),
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=units[county],
                        area_relation="within",
                        area_id=areas[county],
                        kind="standard",
                        valid_from=date.fromisoformat(day),
                        valid_to=date.fromisoformat(day) + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=unit_ids[county],
                        area_id=district_area,
                        valid_from=date.fromisoformat(day),
                        valid_to=date.fromisoformat(day) + timedelta(days=1),
                    )
            merged: dict[tuple[str, object], int] = {}
            for county, column, votes in rows.select("county", "column", "votes").iter_rows():
                merged[(county, targets[column])] = merged.get((county, targets[column]), 0) + votes
            for (county, option), votes in merged.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_ids[county],
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
