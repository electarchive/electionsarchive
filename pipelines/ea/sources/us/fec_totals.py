from ea.store import Connection


def labels(conn: Connection, key: str) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for label, total in conn.execute(
        """select o.ballot_label, sum(v.value)::bigint from external_id e
           join contest_option o on o.contest_id = e.entity_id
           join result_set rs on rs.contest_id = e.entity_id and rs.is_default
           join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
           where e.scheme = 'us.contest' and e.value = %s and o.kind = 'candidate'
           group by 1""",
        [key],
    ).fetchall():
        found.setdefault(int(total), []).append(label)
    return found
