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
                       left join outcome x on x.result_set_id = rs.id and x.option_id = o.id and x.candidacy_id is null
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
                   where x.result_set_id = %(rs)s and x.candidacy_id is null""",
                election,
            ).fetchall()
        )
        won = dict(
            conn.execute(
                """select p.ballot_label, count(*) from contest c
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join outcome x on x.result_set_id = rs.id and x.kind in ('won', 'elected') and x.candidacy_id is null
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
                   where x.result_set_id = %s and x.candidacy_id is null""",
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

        people: dict[str, tuple[str, str | None]] = {}

        def who(person: dict[str, str | int | None]) -> str:
            people[str(person["id"])] = (str(person["name"]), None if person["born"] is None else str(person["born"]))
            return str(person["id"])

        def labeled(value: object) -> object:
            if isinstance(value, dict):
                return {key: labeled(item) for key, item in value.items()}
            if isinstance(value, list):
                return [labeled(item) for item in value]
            if isinstance(value, str) and value in people:
                name, born = people[value]
                shared = [other for other, (text, _) in people.items() if text == name]
                if len(shared) == 1:
                    return name
                years = [(people[other][1] or "")[:4] or None for other in shared]
                year = None if born is None else born[:4]
                if years.count(year) == 1:
                    return f"{name} ({year})"
                if sum(people[other][1] == born for other in shared) > 1:
                    raise ValueError(f"two people named {name} born on {born}")
                return f"{name} ({born})"
            return value

        person = """jsonb_build_object('id', {0}::text,
                      'name', (select n.text from name n where n.entity_id = {0} order by n.is_preferred desc limit 1),
                      'born', (select pe.birth_date::text from person pe where pe.id = {0}))"""
        lands = []
        for land, party, seats, winners, members in conn.execute(
            f"""select (select n.text from name n where n.entity_id = c.district_area_id and n.is_preferred limit 1),
                       o.ballot_label, w.seats,
                       (select jsonb_agg(jsonb_build_object(
                                 'person', {person.format("cy.person_id")},
                                 'votes', (select sum(v.value) from result_rollup v
                                           where v.result_set_k = ks.k and v.area_k = ka.k and v.option_k = ko.k
                                             and v.measure = 1 and v.count_round = 0),
                                 'valid', (select sum(v.value) from result_rollup v
                                           where v.result_set_k = ks.k and v.area_k = ka.k and v.option_k = 0
                                             and v.measure = 4 and v.count_round = 0),
                                 'seated', x.kind = 'elected') order by ka.k)
                        from contest k
                        join area_parent ap on ap.area_id = k.district_area_id and ap.parent_id = c.district_area_id
                        join area ka on ka.id = k.district_area_id
                        join result_set ks on ks.contest_id = k.id and ks.is_default
                        join outcome x on x.result_set_id = ks.id and x.kind in ('won', 'elected')
                          and x.candidacy_id is null
                        join contest_option ko on ko.id = x.option_id and ko.party_id = o.party_id
                        join candidacy cy on cy.option_id = ko.id
                        where k.election_id = c.election_id and k.kind = 'body_seats'),
                       (select jsonb_agg(jsonb_build_object(
                                 'person', {person.format("ca.person_id")}, 'rank', ca.list_position,
                                 'elected', exists (select from outcome m
                                                    where m.candidacy_id = ca.id and m.result_set_id = rs.id))
                               order by ca.list_position)
                        from candidacy ca where ca.option_id = o.id)
                from contest c
                join election e on e.id = c.election_id and e.date_from = '2025-02-23'
                join polity p on p.id = e.polity_id and p.iso_code = 'DE'
                join result_set rs on rs.contest_id = c.id and rs.is_default
                join contest_option o on o.contest_id = c.id
                join outcome w on w.option_id = o.id and w.result_set_id = rs.id and w.candidacy_id is null
                  and w.kind = 'won'
                where c.allocation_parent_id is not null
                order by 1, 2"""
        ):
            lands.append(
                {
                    "land": land,
                    "party": party,
                    "seats": seats,
                    "winners": [
                        {
                            "id": who(winner["person"]),
                            "votes": number(winner["votes"]),
                            "valid": number(winner["valid"]),
                            "seated": winner["seated"],
                        }
                        for winner in winners or []
                    ],
                    "members": [
                        {"id": who(member["person"]), "rank": member["rank"], "elected": member["elected"]}
                        for member in members or []
                    ],
                }
            )
        write("germany-2025-lists", labeled(lands))

        people.clear()
        write(
            "germany-ep-2024-lists",
            labeled(
                [
                    {
                        "list": label,
                        "seats": seats,
                        "members": [
                            {"id": who(member["person"]), "rank": member["rank"], "elected": member["elected"]}
                            for member in members
                        ],
                    }
                    for label, seats, members in conn.execute(
                        f"""select concat_ws(' ', o.ballot_label,
                                 (select n.text from name n where n.entity_id = c.district_area_id and n.is_preferred
                                  and c.party_scope_id is not null limit 1)),
                               w.seats,
                               jsonb_agg(jsonb_build_object(
                                 'person', {person.format("ca.person_id")}, 'rank', ca.list_position,
                                 'elected', exists (select from outcome m
                                                    where m.candidacy_id = ca.id and m.result_set_id = rs.id))
                                 order by ca.list_position)
                        from contest c
                        join election e on e.id = c.election_id and e.date_from = '2024-06-09'
                        join polity p on p.id = e.polity_id and p.iso_code = 'DE'
                        join result_set rs on rs.contest_id = c.id and rs.is_default
                        join contest_option o on o.contest_id = c.id
                        join outcome w on w.option_id = o.id and w.result_set_id = rs.id and w.candidacy_id is null
                          and w.kind = 'won'
                        join candidacy ca on ca.option_id = o.id
                        group by c.id, o.id, w.seats order by 1"""
                    )
                ]
            ),
        )

        people.clear()
        colleges: dict[str, list[dict[str, object]]] = {}
        for college, label, seats, votes, valid, members in conn.execute(
            f"""select (select n.text from name n where n.entity_id = c.id and n.is_preferred limit 1), o.ballot_label,
                       coalesce(w.seats, 0),
                       (select sum(v.value) from result_rollup v where v.result_set_k = counted.k and v.area_k = a.k
                          and v.option_k = o.k and v.measure = 1 and v.count_round = 0),
                       (select sum(v.value) from result_rollup v where v.result_set_k = counted.k and v.area_k = a.k
                          and v.option_k <> 0 and v.measure = 1 and v.count_round = 0),
                       (select jsonb_agg(jsonb_build_object(
                                 'person', {person.format("ca.person_id")}, 'rank', ca.list_position,
                                 'elected', exists (select from outcome m
                                                    where m.candidacy_id = ca.id and m.result_set_id = rs.id))
                               order by ca.list_position)
                        from candidacy ca where ca.option_id = o.id)
                from contest c
                join contest parent on parent.id = c.allocation_parent_id
                join election e on e.id = c.election_id and e.date_from = '2022-09-25'
                join polity p on p.id = e.polity_id and p.iso_code = 'IT'
                join area a on a.id = c.district_area_id
                join result_set rs on rs.contest_id = c.id and rs.stage = 'official'
                join result_set counted on counted.contest_id = c.id and counted.stage = 'preliminary'
                join contest_option o on o.contest_id = c.id and o.kind = 'list'
                left join outcome w on w.option_id = o.id and w.result_set_id = rs.id and w.candidacy_id is null
                  and w.kind = 'won'
                order by 1, 2"""
        ):
            colleges.setdefault(college, []).append(
                {
                    "list": label,
                    "seats": seats,
                    "votes": number(votes),
                    "valid": number(valid),
                    "members": [
                        {"id": who(member["person"]), "rank": member["rank"], "elected": member["elected"]}
                        for member in members or []
                    ],
                }
            )
        transferred = [
            {"college": college, "list": label, "id": who(holder)}
            for college, label, holder in conn.execute(
                f"""select (select n.text from name n where n.entity_id = o.contest_id and n.is_preferred limit 1),
                           o.ballot_label, {person.format("ca.person_id")}
                    from outcome m join candidacy ca on ca.id = m.candidacy_id
                    join contest_option o on o.id = m.option_id
                    join result_set rs on rs.id = m.result_set_id and rs.stage = 'official'
                    join contest c on c.id = o.contest_id join election e on e.id = c.election_id
                    where e.date_from = '2022-09-25' and ca.option_id <> m.option_id"""
            )
        ]
        winners = sorted(
            who(winner)
            for (winner,) in conn.execute(
                f"""select {person.format("ca.person_id")}
                    from contest c
                    join election e on e.id = c.election_id and e.date_from = '2022-09-25'
                    join polity p on p.id = e.polity_id and p.iso_code = 'IT'
                    join result_set rs on rs.contest_id = c.id and rs.stage = 'official'
                    join outcome x on x.result_set_id = rs.id and x.kind = 'elected' and x.candidacy_id is null
                    join candidacy ca on ca.option_id = x.option_id
                    where c.kind = 'body_seats' and c.magnitude = 1"""
            )
        )
        write(
            "italy-2022-lists",
            labeled(
                {
                    "winners": winners,
                    "transferred": transferred,
                    "colleges": [{"college": college, "lists": lists} for college, lists in colleges.items()],
                }
            ),
        )

        for fixture, iso, measure in (("zurich-2023-lists", "CH", 10), ("acre-2022-lists", "BR", 1)):
            people.clear()
            valid = 0
            slates = []
            for label, seats, total, members in conn.execute(
                f"""select o.ballot_label, coalesce(w.seats, 0),
                           (select coalesce(sum(v.value), 0) from result_rollup v join area a on a.k = v.area_k
                            where v.result_set_k = rs.k and a.id = c.district_area_id and v.option_k <> 0
                              and v.measure = 1 and v.count_round = 0),
                           (select jsonb_agg(jsonb_build_object(
                                     'person', {person.format("ca.person_id")}, 'rank', ca.list_position,
                                     'votes', (select sum(v.value) from result_rollup v join area a on a.k = v.area_k
                                               where v.result_set_k = rs.k and a.id = c.district_area_id
                                                 and v.option_k = co.k and v.measure = %(measure)s
                                                 and v.count_round = 0),
                                     'elected', exists (select from outcome x where x.option_id = co.id
                                                        and x.result_set_id = rs.id and x.kind = 'elected'),
                                     'disqualified', exists (select from outcome x where x.option_id = co.id
                                                             and x.result_set_id = rs.id and x.kind = 'disqualified'))
                                   order by ca.list_position, co.ballot_label)
                            from contest_option co join candidacy ca on ca.option_id = co.id
                            where co.parent_option_id = o.id)
                    from contest c
                    join election e on e.id = c.election_id
                    join polity p on p.id = e.polity_id and p.iso_code = %(iso)s
                    join result_set rs on rs.contest_id = c.id and rs.is_default
                    join contest_option o on o.contest_id = c.id and o.kind = 'list'
                    left join outcome w on w.option_id = o.id and w.result_set_id = rs.id and w.candidacy_id is null
                      and w.kind in ('won', 'elected') and w.seats is not null
                    where exists (select from contest_option co where co.parent_option_id = o.id)
                    order by 1""",
                {"iso": iso, "measure": measure},
            ):
                valid = number(total)
                slates.append(
                    {
                        "list": label,
                        "seats": seats,
                        "members": [
                            {
                                "id": who(member["person"]),
                                "rank": member["rank"],
                                "votes": number(member["votes"] or 0),
                                "elected": member["elected"],
                                "disqualified": member["disqualified"],
                            }
                            for member in members
                        ],
                    }
                )
            write(fixture, labeled({"valid": valid, "lists": slates}))
    return sorted(path.name for path in FIXTURES.iterdir())
