from difflib import SequenceMatcher

from psycopg.types.json import Jsonb

from ea import coverage
from ea.store import connect

FIELDS = ("polity", "state", "level", "stage", "years", "grain")
SAME_LABEL = 0.6

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
returning summary
"""


def rekeys(conn, rows: list[dict]) -> list[tuple[str, int, str]]:
    keys = {row["key"] for row in rows}
    columns = ("number", "gap_key", "status", "ids", "label", *FIELDS)
    held = [dict(zip(columns, found)) for found in conn.execute(f"select {', '.join(columns)} from bounty")]
    known = {bounty["gap_key"] for bounty in held}
    fresh = [row for row in rows if row["key"] not in known]
    pairs = sorted(
        (
            (SequenceMatcher(None, bounty["label"], row["label"]).ratio(), bounty["number"], bounty, row)
            for bounty in held
            if bounty["status"] == "open" and bounty["gap_key"] not in keys
            for row in fresh
            if all(bounty[field] == row[field] for field in FIELDS)
            and (not bounty["ids"] or not row["ids"] or set(bounty["ids"]) & set(row["ids"]))
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )
    moved: list[tuple[str, int, str]] = []
    taken: set[str] = set()
    for score, number, bounty, row in pairs:
        if score >= SAME_LABEL and bounty["gap_key"] not in taken and row["key"] not in taken:
            taken |= {bounty["gap_key"], row["key"]}
            moved.append((row["key"], number, row["label"]))
    return moved


def sync() -> dict[str, int | list[str]]:
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
                "checked": row["checked"],
            }
            for row in coverage.registry()
            if row["category"] in ("known_gap", "blocked")
        }.values()
    )
    if not rows:
        raise ValueError("the gap registry is empty, so no bounty would stay open")
    with connect() as conn:
        moved = rekeys(conn, rows)
        for key, number, _ in moved:
            conn.execute("update bounty set gap_key = %s where number = %s", [key, number])
        added = conn.execute(UPSERT_SQL, [Jsonb(rows)]).fetchall()
        closed = conn.execute(CLOSE_SQL, [[row["key"] for row in rows]]).fetchall()
        conn.commit()
    return {
        "bounties": len(rows),
        "added": sum(1 for (new,) in added if new),
        "rekeyed": [f"Bounty {number} now: {label}" for _, number, label in moved],
        "closed": [summary for (summary,) in closed],
    }
