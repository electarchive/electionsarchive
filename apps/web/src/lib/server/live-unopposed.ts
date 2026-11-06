import type { LiveUnopposed } from "@ea/analytics/live";
import { sql } from "drizzle-orm";
import { liveDb as db } from "#lib/server/db/index.js";

export async function unopposed(contests: Array<string>) {
  const found = await db.execute<{
    contest: string;
    options: Array<string>;
    tacit: boolean;
  }>(sql`
    with named as (
      select c.id as contest, c.default_result_kind = 'tacit' as tacit,
        case when b.kind = 'electoral_college' then 1 else coalesce(c.magnitude, 1) end as seats,
        array_agg(o.id order by o.ballot_position nulls last, o.k) as options
      from contest c
      left join body b on b.id = c.body_id
      join contest_option o on o.contest_id = c.id and o.parent_option_id is null and o.kind in ('candidate', 'ticket', 'list')
      where c.id = any(${sql.param(contests)}::uuid[]) and c.kind <> 'ballot_question'
      group by c.id, b.kind
    )
    select n.contest, n.options::text[] as options, n.tacit
    from named n
    where cardinality(n.options) <= n.seats
      and (cardinality(n.options) = 1 or not exists (
        select from label l
        where l.entity_id = n.contest and l.kind = 'data_quality' and l.value = 'seats_unverified'
      ))
      and not exists (
        select from contest_option o
        where o.contest_id = n.contest and o.parent_option_id is null and (
          o.kind = 'answer' or (
            o.kind = 'write_in' and (
              exists (select from candidacy y where y.option_id = o.id)
              or coalesce(o.ballot_label, '') !~* '^(write-?\\s?ins?( votes| totals)?|other write-in votes|scatter(ed|ing)?|(all )?others?( votes)?|not assigned)?$'
            )
          )
        )
      )
      and (n.tacit or not exists (
        select from contest_option o join changeset cs on cs.id = o.changeset_id
        where o.id = any(n.options) and cs.pipeline = 'us.fec_2026' and not exists (
          select from candidacy y join citation ci on ci.entity_id = y.id join source s on s.id = ci.source_id
          where y.option_id = any(array(select o.id union all select m.id from contest_option m where m.parent_option_id = o.id))
            and coalesce(s.url, '') !~ 'fec[.]gov'
        )
      ))
  `);
  return new Map<string, LiveUnopposed>(
    found.rows.map((row) => [
      row.contest,
      { options: row.options, isTacit: row.tacit },
    ]),
  );
}
