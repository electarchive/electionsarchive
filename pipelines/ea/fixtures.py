import json
from decimal import Decimal
from typing import LiteralString

from ea.store import ROOT, connect

FIXTURES = ROOT / "packages" / "engine" / "src" / "fixtures"


def number(value: object) -> int:
    if not isinstance(value, Decimal | int) or value != int(value):
        raise ValueError(f"{value!r} is not a whole number")
    return int(value)


def write(name: str, data: object) -> None:
    (FIXTURES / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def export() -> list[str]:
    with connect() as conn:

        def contest(path: str) -> tuple[object, int, object]:
            row = conn.execute(
                """select c.id, c.magnitude, rs.id from slug s join contest c on c.id = s.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default where s.path = %s""",
                [path],
            ).fetchone()
            if row is None:
                raise LookupError(f"no contest with a default result set at {path}")
            return row

        def totals(query: LiteralString, params: dict[str, object]) -> list[tuple[str, int]]:
            return [(label, number(votes)) for label, votes in conn.execute(query, params).fetchall()]

        for fixture, path in (
            ("turkey-2023", "tr/2023-05-14-general/parliament"),
            ("turkey-2018", "tr/2018-06-24-general/parliament"),
            ("turkey-2015-11", "tr/2015-11-01-general/parliament"),
            ("turkey-2015-06", "tr/2015-06-07-general/parliament"),
            ("turkey-2011", "tr/2011-06-12-general/parliament"),
            ("turkey-2007", "tr/2007-07-22-general/parliament"),
        ):
            national, _, _ = contest(path)
            tree = {"national": national}
            valid = conn.execute(
                """select sum(v.value) from result_value v join result_set rs on rs.k = v.result_set_k and rs.is_default
                   join contest c on c.id = rs.contest_id
                   where %(national)s in (c.id, c.allocation_parent_id) and v.measure = 4 and v.option_k = 0""",
                tree,
            ).fetchone()
            independents = dict(
                conn.execute(
                    """select n.text, coalesce(sum(x.seats), 0) from contest c
                       join name n on n.entity_id = c.id and n.is_preferred
                       join result_set rs on rs.contest_id = c.id and rs.is_default
                       left join contest_option o on o.contest_id = c.id and o.kind = 'others'
                       left join outcome x on x.result_set_id = rs.id and x.option_id = o.id
                       where c.allocation_parent_id = %(national)s group by 1""",
                    tree,
                ).fetchall()
            )
            districts: dict[str, dict[str, object]] = {}
            lists: dict[str, list[dict[str, object]]] = {}
            groups: dict[str, int] = {}
            for district, magnitude, label, group, votes, seats in conn.execute(
                """select n.text, c.magnitude, o.ballot_label, coalesce(an.text, o.ballot_label),
                          sum(v.value) filter (where v.measure in (1, 19)),
                          (select x.seats from outcome x
                           where x.result_set_id = rs.id and x.option_id = o.id and x.seats is not null)
                   from contest c
                   join name n on n.entity_id = c.id and n.is_preferred
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k
                   join contest_option o on o.k = v.option_k and o.party_id is not null
                   left join name an on an.entity_id = o.alliance_id and an.is_preferred
                   where c.allocation_parent_id = %(national)s
                   group by n.text, c.magnitude, rs.id, o.id, an.text order by 1, 5 desc, 3""",
                tree,
            ):
                districts.setdefault(
                    district,
                    {
                        "name": district,
                        "seats": magnitude,
                        "independents": number(independents[district]),
                        "lists": lists.setdefault(district, []),
                    },
                )
                lists[district].append({"id": label, "group": group, "votes": number(votes), "declared": seats or 0})
                groups[group] = groups.get(group, 0) + number(votes)
            write(
                fixture,
                {
                    "valid": number(None if valid is None else valid[0]),
                    "groups": [
                        {"id": group, "votes": votes} for group, votes in sorted(groups.items(), key=lambda g: -g[1])
                    ],
                    "districts": list(districts.values()),
                },
            )

        allocation, seats, allocation_rs = contest("de/2025-02-23-general/bundestag")
        election = {"allocation": allocation, "rs": allocation_rs}
        second = totals(
            """select o.ballot_label, sum(v.value) from contest c
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join result_value v on v.result_set_k = rs.k and v.measure = 1
               join contest_option o on o.k = v.option_k and o.party_id is not null
               where c.allocation_parent_id = %(allocation)s group by 1 order by 2 desc""",
            election,
        )
        declared = dict(
            conn.execute(
                """select o.ballot_label, x.seats from outcome x join contest_option o on o.id = x.option_id
                   where x.result_set_id = %(rs)s""",
                election,
            ).fetchall()
        )
        won = dict(
            conn.execute(
                """select p.ballot_label, count(*) from contest c
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join outcome x on x.result_set_id = rs.id and x.kind in ('won', 'elected')
                   join contest_option o on o.id = x.option_id
                   join contest_option p on p.party_id = o.party_id and p.contest_id = %(allocation)s
                   where c.kind = 'body_seats'
                     and c.election_id = (select election_id from contest where id = %(allocation)s)
                   group by 1""",
                election,
            ).fetchall()
        )
        write(
            "germany-2025",
            {
                "seats": seats,
                "valid": sum(votes for _, votes in second),
                "parties": [
                    {
                        "id": label,
                        "votes": votes,
                        "constituencies": won.get(label, 0),
                        "declared": declared.get(label, 0),
                    }
                    for label, votes in second
                ],
            },
        )

        _, seats, european_rs = contest("de/2024-06-09-european-parliament/european-parliament")
        lists_votes = totals(
            """select o.ballot_label, sum(v.value) from result_value v
               join result_set rs on rs.k = v.result_set_k and rs.id = %(rs)s
               join contest_option o on o.k = v.option_k
               where v.measure = 1 group by 1 order by 2 desc""",
            {"rs": european_rs},
        )
        declared = dict(
            conn.execute(
                """select o.ballot_label, x.seats from outcome x join contest_option o on o.id = x.option_id
                   where x.result_set_id = %s""",
                [european_rs],
            ).fetchall()
        )
        write(
            "germany-ep-2024",
            {
                "seats": seats,
                "lists": [
                    {"id": label, "votes": votes, "declared": declared.get(label, 0)} for label, votes in lists_votes
                ],
            },
        )

        def questions(iso: str, scheme: str) -> list[tuple[str, str, int, str | None, str | None]]:
            return conn.execute(
                """select n.text, bq.type::text, rs.k,
                          (select o.ballot_label from outcome x join contest_option o on o.id = x.option_id
                           where x.result_set_id = rs.id and x.kind = 'won'),
                          (select x.notes from outcome x where x.result_set_id = rs.id and x.notes is not null limit 1)
                   from contest c join ballot_question bq on bq.contest_id = c.id
                   join name n on n.entity_id = c.id and n.is_preferred
                   join election e on e.id = c.election_id join polity p on p.id = e.polity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   where p.iso_code = %s and bq.type <> 'tie_break'
                     and exists (select from result_rollup r join area a on a.k = r.area_k
                                 where r.result_set_k = rs.k and a.scheme_code = %s)
                   order by 1""",
                [iso, scheme],
            ).fetchall()

        def rollup(result_set_k: int, scheme: str) -> list[tuple[str, dict[str, int]]]:
            units: dict[str, dict[str, int]] = {}
            for code, label, value in conn.execute(
                """select coalesce(ac.code, ''), coalesce(o.ballot_label, m.code), sum(r.value)
                   from result_rollup r join area a on a.k = r.area_k and a.scheme_code = %s
                   left join area_code ac on ac.area_id = a.id
                   join measure m on m.id = r.measure
                   left join contest_option o on o.k = r.option_k and r.option_k <> 0
                   where r.result_set_k = %s and r.count_round = 0 and (r.option_k = 0 or r.measure = 1)
                   group by 1, 2""",
                [scheme, result_set_k],
            ):
                units.setdefault(code, {})[label] = number(value)
            return sorted(units.items())

        write(
            "switzerland-2010-11-28",
            [
                {
                    "question": question,
                    "type": kind,
                    "won": outcome,
                    "cantons": [
                        {"id": code, "yes": values["Yes"], "no": values["No"]}
                        for code, values in rollup(result_set_k, "ch.canton")
                    ],
                }
                for question, kind, result_set_k, outcome, _ in questions("CH", "ch.canton")
            ],
        )
        referendums = []
        for question, kind, result_set_k, outcome, notes in questions("IT", "it.nation"):
            [(_, values)] = rollup(result_set_k, "it.nation")
            referendums.append(
                {
                    "question": question,
                    "type": kind,
                    "won": outcome,
                    "notes": notes,
                    "registered": values["registered"],
                    "voters": values.get("voters", values.get("ballots_cast")),
                    "yes": values["Sì"],
                    "no": values["No"],
                }
            )
        write("italy-2025-06-08", referendums)
    return sorted(path.name for path in FIXTURES.iterdir())
