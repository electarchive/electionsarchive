import re

from psycopg.types.json import Jsonb

from ea import coverage
from ea.store import connect

FIELDS = ("polity", "state", "level", "stage", "years", "grain")

UPSERT_SQL = """
insert into bounty (gap_key, polity, state, level, stage, years, grain, ids, label, category, reason, sources, checked)
select r ->> 'key', r ->> 'polity', r ->> 'state', r ->> 'level', r ->> 'stage', r ->> 'years', r ->> 'grain',
       array(select jsonb_array_elements_text(r -> 'ids')), r ->> 'label', r ->> 'category', r ->> 'reason',
       r ->> 'sources', nullif(r ->> 'checked', '')::date
from jsonb_array_elements(%s) r
on conflict (gap_key) do update set
  ids = excluded.ids, label = excluded.label, category = excluded.category, reason = excluded.reason,
  sources = excluded.sources, checked = excluded.checked,
  status = case when bounty.status = 'closed' then 'open' else bounty.status end,
  closed_reason = case when bounty.status = 'closed' then null else bounty.closed_reason end,
  closed_at = case when bounty.status = 'closed' then null else bounty.closed_at end
returning (xmax = 0) as added
"""

CLOSE_SQL = """
with closed as (
  update bounty set status = 'closed', closed_reason = 'gap_closed', closed_at = now()
  where status = 'open' and not (gap_key = any(%s))
  returning id, number, polity, state, label
)
insert into activity (kind, bounty_id, polity_ids, summary)
select 'bounty_closed', c.id,
       array(select p.id from polity p
             where p.iso_code = upper(c.polity) || case when c.state = '' then '' else '-' || c.state end limit 1),
       format('Bounty %%s closed: %%s', c.number, c.label)
from closed c
"""


def sync() -> dict[str, int]:
    rows = list(
        {
            coverage.gap_key(row): {field: row[field] for field in FIELDS}
            | {
                "key": coverage.gap_key(row),
                "ids": row["ids"],
                "label": row["label"] or row["reason"],
                "category": row["category"],
                "reason": row["reason"],
                "sources": row["sources"],
                "checked": row["checked"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["checked"] or "") else "",
            }
            for row in coverage.registry()
            if row["category"] in ("known_gap", "blocked")
        }.values()
    )
    if not rows:
        raise ValueError("the gap registry is empty, so no bounty would stay open")
    with connect() as conn:
        added = conn.execute(UPSERT_SQL, [Jsonb(rows)]).fetchall()
        closed = conn.execute(CLOSE_SQL, [[row["key"] for row in rows]]).rowcount
        conn.commit()
    return {"bounties": len(rows), "added": sum(1 for (new,) in added if new), "closed": closed}
