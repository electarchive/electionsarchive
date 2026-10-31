import json
import os

from ea.store import connect

with connect() as c:
    rows = c.execute("""select x.value, u.code, upper(o.ballot_label), v.value::bigint
        from external_id x join contest c on c.id=x.entity_id join election e on e.id=c.election_id
        join result_set rs on rs.contest_id=c.id and rs.is_default join result_value v on v.result_set_k=rs.k and v.measure=1
        join contest_option o on o.k=v.option_k join reporting_unit u on u.k=v.unit_k
        where x.scheme='us.contest' and x.value like '%%:OR:question:%%' and e.date_from='2018-11-06'""").fetchall()
out = {}
for key, unit, label, value in rows:
    out.setdefault(key, {}).setdefault(unit, {}).setdefault(label, 0)
    out[key][unit][label] += value
print(json.dumps(out, indent=1)[:600])
json.dump(out, open(os.environ["OR_WORK"] + "/tot.json", "w"))
