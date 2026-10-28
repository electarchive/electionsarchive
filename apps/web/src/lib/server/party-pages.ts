import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { contents } from "#lib/server/pages.js";

export async function rebuild() {
  const started = Date.now();
  const listed = await db.execute<{
    id: string;
    stale: boolean;
    old: boolean;
  }>(sql`
    with recursive
      used as (
        select distinct party_id as id from contest_option where party_id is not null
      ),
      owners as (
        select coalesce(m.into_id, u.id) as id from used u left join entity_merge m on m.entity_id = u.id
        union
        select pa.parent_party_id from used u
        join party_affiliation pa on pa.party_id = u.id and pa.kind = 'regional_branch'
      ),
      listed as (
        select o.id from owners o join entity e on e.id = o.id
        where e.retired_by is null and not exists (select from entity_merge m where m.entity_id = o.id)
      ),
      since as (select greatest(coalesce(min(built_at), now()), now() - interval '2 days') as at from party_page),
      elections(id, at) as (
        select id, recorded_from from election where recorded_from > (select at from since)
        union all select id, upper(recorded) from election_history where upper(recorded) > (select at from since)
      ),
      systems(id, at) as (
        select id, recorded_from from electoral_system where recorded_from > (select at from since)
        union all select id, upper(recorded) from electoral_system_history where upper(recorded) > (select at from since)
      ),
      touched(contest_id, at) as (
        select id, recorded_from from contest where recorded_from > (select at from since)
        union all select id, upper(recorded) from contest_history where upper(recorded) > (select at from since)
        union all select contest_id, recorded_from from contest_option where recorded_from > (select at from since)
        union all select contest_id, recorded_from from result_set where recorded_from > (select at from since)
        union all select contest_id, upper(recorded) from result_set_history where upper(recorded) > (select at from since)
        union all select r.contest_id, o.recorded_from from outcome o join result_set r on r.id = o.result_set_id
          where o.recorded_from > (select at from since)
        union all select r.contest_id, upper(o.recorded) from outcome_history o join result_set r on r.id = o.result_set_id
          where upper(o.recorded) > (select at from since)
        union all select c.id, x.at from elections x join election e on e.id = x.id or e.parent_id = x.id
          join contest c on c.election_id = e.id
        union all select c.id, x.at from systems x join contest c on c.electoral_system_id = x.id
      ),
      latest as materialized (select contest_id, max(at) as at from touched group by contest_id),
      up(contest_id, at) as (
        select contest_id, at from latest
        union select c.allocation_parent_id, u.at from up u join contest c on c.id = u.contest_id
          where c.allocation_parent_id is not null
      ),
      down(contest_id, at) as (
        select contest_id, at from up
        union select c.id, d.at from down d join contest c on c.allocation_parent_id = d.contest_id
      ),
      raw(party_id, at) as (
        select o.party_id, d.at from down d join contest_option o on o.contest_id = d.contest_id where o.party_id is not null
        union all select party_id, upper(recorded) from contest_option_history
          where upper(recorded) > (select at from since) and party_id is not null
        union all select id, recorded_from from party where recorded_from > (select at from since)
        union all select id, upper(recorded) from party_history where upper(recorded) > (select at from since)
        union all select n.entity_id, n.recorded_from from party p join name n on n.entity_id = p.id
          where n.recorded_from > (select at from since)
        union all select n.entity_id, upper(n.recorded) from name_history n
          where upper(n.recorded) > (select at from since) and exists (select from party p where p.id = n.entity_id)
        union all select s.entity_id, s.recorded_from from party p join slug s on s.entity_id = p.id
          where s.recorded_from > (select at from since)
        union all select party_id, recorded_from from party_color where recorded_from > (select at from since)
        union all select party_id, upper(recorded) from party_color_history where upper(recorded) > (select at from since)
        union all select unnest(array[from_party_id, to_party_id]), recorded_from from party_relation
          where recorded_from > (select at from since)
        union all select unnest(array[from_party_id, to_party_id]), upper(recorded) from party_relation_history
          where upper(recorded) > (select at from since)
        union all select unnest(array[party_id, parent_party_id]), recorded_from from party_affiliation
          where recorded_from > (select at from since)
        union all select unnest(array[party_id, parent_party_id]), upper(recorded) from party_affiliation_history
          where upper(recorded) > (select at from since)
        union all select unnest(array[entity_id, into_id]), recorded_from from entity_merge
          where recorded_from > (select at from since)
        union all select unnest(array[entity_id, into_id]), upper(recorded) from entity_merge_history
          where upper(recorded) > (select at from since)
      ),
      owned as materialized (
        select r.party_id as id, r.at from raw r
        union all select m.into_id, r.at from raw r join entity_merge m on m.entity_id = r.party_id
      ),
      expanded(id, at) as (
        select id, at from owned
        union all select pa.parent_party_id, o.at from owned o
          join party_affiliation pa on pa.party_id = o.id and pa.kind = 'regional_branch'
        union all select coalesce(m.into_id, pr.to_party_id), o.at from owned o
          join party_relation pr on pr.kind = 'merged_into' and pr.from_party_id = o.id
          left join entity_merge m on m.entity_id = pr.to_party_id
      ),
      changed as (select id, max(at) as at from expanded group by id)
    select l.id, p.party_id is null or x.at > p.built_at as stale, p.built_at < now() - interval '1 day' as old
    from listed l left join party_page p on p.party_id = l.id left join changed x on x.id = l.id
    order by p.built_at nulls first, l.id
  `);
  const stale = listed.rows.filter((row) => row.stale).map((row) => row.id);
  const refreshed = listed.rows
    .filter((row) => !row.stale && row.old)
    .slice(0, Math.ceil(listed.rows.length / 8))
    .map((row) => row.id);
  const queue = [...refreshed.toReversed(), ...stale];
  let built = 0;
  let failed = 0;
  await Promise.all(
    Array.from({ length: 2 }, async () => {
      for (let id = queue.pop(); id != null; id = queue.pop()) {
        try {
          const read = new Date().toISOString();
          const page = await contents(id);
          await db.execute(sql`
            insert into party_page (party_id, page, built_at)
            values (${id}::uuid, ${JSON.stringify(page)}::jsonb, ${read}::timestamptz)
            on conflict (party_id) do update set page = excluded.page, built_at = excluded.built_at
          `);
          built += 1;
        } catch (error) {
          failed += 1;
          console.error(id, error);
        }
      }
    }),
  );
  const removed = await db.execute<Record<string, never>>(sql`
    delete from party_page
    where not (party_id = any(${sql.param(listed.rows.map((row) => row.id))}::uuid[]))
  `);
  await db.execute(sql`analyze party_page`);
  return `${built} of ${stale.length} stale and ${refreshed.length} day-old party pages rebuilt (${listed.rows.length} listed), ${failed} failed, ${removed.rowCount ?? 0} removed, in ${Math.round((Date.now() - started) / 1000)}s`;
}
