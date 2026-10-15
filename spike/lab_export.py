import json
import sys
from collections import defaultdict
from pathlib import Path

import psycopg

out = Path(sys.argv[1])
conn = psycopg.connect("dbname=ea_spike host=127.0.0.1 port=5432 user=postgres")
cur = conn.cursor()

row = cur.execute(
    """select c.election_id from contest c join name n on n.entity_id = c.id
       where n.text = '2025 Bundestag election, national seat allocation'"""
).fetchone()
assert row is not None
election = row[0]

row = cur.execute(
    """with wk as (
         select c.code, st_scale(st_transform(g.geom, 25832), 0.001, 0.001) as g
         from area a join area_code c on c.area_id = a.id join area_geometry g on g.area_id = a.id
         where a.scheme_code = 'de.wahlkreis'
       ),
       simple as (select code, st_coveragesimplify(g, 0.9) over () as g from wk),
       box as (select st_xmin(st_extent(g)) as x0, st_ymax(st_extent(g)) as y1,
                      st_xmax(st_extent(g)) as x1, st_ymin(st_extent(g)) as y0 from simple),
       moved as (select s.code, st_translate(s.g, -b.x0, -b.y1) as g from simple s, box b),
       land as (
         select pc.code as land, m.g from moved m
         join area_code c on c.code = m.code
         join area a on a.id = c.area_id and a.scheme_code = 'de.wahlkreis'
         join area_parent p on p.area_id = a.id
         join area_code pc on pc.area_id = p.parent_id
       )
       select json_build_object(
         'width', round((b.x1 - b.x0)::numeric, 1), 'height', round((b.y1 - b.y0)::numeric, 1),
         'constituencies', (select json_object_agg(code, st_assvg(g, 1, 1)) from moved),
         'laender', (select json_object_agg(land, st_assvg(g, 1, 1)) from
                       (select land, st_union(g, 0.05) as g from land group by land) u),
         'centroids', (select json_object_agg(code, json_build_array(round(st_x(p)::numeric, 1), round(-st_y(p)::numeric, 1)))
                       from (select code, st_pointonsurface(g) as p from moved) q)
       )
       from box b"""
).fetchone()
assert row is not None
geometry = row[0]

names = dict(
    cur.execute(
        """select c.code, n.text from area a join area_code c on c.area_id = a.id
           join name n on n.entity_id = a.id and n.kind = 'official'
           where a.scheme_code in ('de.wahlkreis', 'de.land')"""
    ).fetchall()
)
land_of = dict(
    cur.execute(
        """select c.code, pc.code from area a join area_code c on c.area_id = a.id
           join area_parent p on p.area_id = a.id join area_code pc on pc.area_id = p.parent_id
           where a.scheme_code = 'de.wahlkreis'"""
    ).fetchall()
)

counts: dict[str, dict[str, int]] = defaultdict(dict)
ranked: dict[str, dict[str, list[tuple[str, int]]]] = {
    code: {"first": [], "second": []} for code in geometry["constituencies"]
}
outcomes: dict[str, tuple[str, bool, str | None]] = {}

for code, measure, value in cur.execute(
    """select u.code, v.measure, v.value from result_set r
       join result_value v on v.result_set_k = r.k and v.option_k = 0
       join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'de.wahlkreis'
       where r.election_id = %s and r.is_default and r.contest_id is null""",
    [election],
):
    counts[code][{2: "registered", 14: "voters"}[measure]] = int(value)

for vote, kind in (("first", "body_seats"), ("second", "list_allocation")):
    for code, option, measure, value in cur.execute(
        """select u.code, o.ballot_label, v.measure, v.value from contest c
           join result_set r on r.contest_id = c.id and r.is_default
           join result_value v on v.result_set_k = r.k
           join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'de.wahlkreis'
           left join contest_option o on o.k = v.option_k
           where c.election_id = %s and c.kind = %s and c.allocation_parent_id is not distinct from
             (case when %s = 'body_seats' then null else c.allocation_parent_id end)""",
        [election, kind, kind],
    ):
        if measure == 1:
            ranked[code][vote].append((option, int(value)))
        elif measure in (4, 5):
            counts[code][f"{'valid' if measure == 4 else 'invalid'}_{vote}"] = int(value)

for code, option, outcome, member in cur.execute(
    """select u.code, o.ballot_label, oc.kind, (
         select n.text from candidacy ca join name n on n.entity_id = ca.person_id where ca.option_id = o.id limit 1
       )
       from contest c
       join result_set r on r.contest_id = c.id and r.is_default
       join outcome oc on oc.result_set_id = r.id
       join contest_option o on o.id = oc.option_id
       join reporting_unit u on u.area_id = c.district_area_id and u.scheme_code = 'de.wahlkreis'
       where c.election_id = %s and c.kind = 'body_seats'""",
    [election],
):
    outcomes[code] = (option, outcome == "elected", member)

for votes in ranked.values():
    for vote in ("first", "second"):
        votes[vote].sort(key=lambda pair: -pair[1])

seats = dict(
    cur.execute(
        """select o.ballot_label, oc.seats from contest c join name n on n.entity_id = c.id
           join result_set r on r.contest_id = c.id join outcome oc on oc.result_set_id = r.id
           join contest_option o on o.id = oc.option_id
           where n.text = '2025 Bundestag election, national seat allocation'"""
    ).fetchall()
)
party_names = dict(
    cur.execute(
        """select distinct o.ballot_label, n.text from contest c join contest_option o on o.contest_id = c.id
           join name n on n.entity_id = o.party_id and n.kind = 'official'
           where c.election_id = %s""",
        [election],
    ).fetchall()
)

totals: dict[str, dict[str, int]] = defaultdict(lambda: {"first": 0, "second": 0, "won": 0, "unseated": 0})
for code, votes in ranked.items():
    for vote in ("first", "second"):
        for option, value in votes[vote]:
            totals[option][vote] += value
    winner, seated, _ = outcomes[code]
    totals[winner]["won"] += 1
    totals[winner]["unseated"] += not seated

parties = [
    {"code": code, "name": party_names.get(code, code), "seats": seats.get(code, 0), **values}
    for code, values in sorted(totals.items(), key=lambda item: (-item[1]["second"], -item[1]["first"]))
]

summary = {
    key: sum(numbers.get(key, 0) for numbers in counts.values())
    for key in ("registered", "voters", "valid_first", "invalid_first", "valid_second", "invalid_second")
}
for code, votes in ranked.items():
    for vote in ("first", "second"):
        assert sum(value for _, value in votes[vote]) == counts[code][f"valid_{vote}"], code
assert sum(seats.values()) == 630

for votes in ranked.values():
    for vote in ("first", "second"):
        rest = sum(value for _, value in votes[vote][8:])
        votes[vote] = votes[vote][:8] + ([("Others", rest)] if rest else [])


out.write_text(
    json.dumps(
        {
            "summary": summary,
            "seats": 630,
            "parties": parties,
            "laender": {code: names[code] for code in geometry["laender"]},
            "map": {
                "width": float(geometry["width"]),
                "height": float(geometry["height"]),
                "laender": geometry["laender"],
                "centroids": geometry["centroids"],
            },
            "constituencies": [
                {
                    "code": code,
                    "name": names[code],
                    "land": land_of[code],
                    **counts[code],
                    "first": ranked[code]["first"],
                    "second": ranked[code]["second"],
                    "winner": outcomes[code][0],
                    "seated": outcomes[code][1],
                    "member": outcomes[code][2],
                    "path": geometry["constituencies"][code],
                }
                for code in sorted(ranked)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
print(
    out,
    out.stat().st_size,
    summary,
    [(p["code"], p["second"], p["seats"], p["won"], p["unseated"]) for p in parties[:10]],
)
