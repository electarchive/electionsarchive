import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { names } from "#lib/server/rpc/archive.js";

export const NIGHT = 24;

const loaded = new Map<string, ReturnType<typeof load>>();

function unit(seed: string) {
  let hash = 2_166_136_261;
  for (const char of seed) {
    hash = Math.imul(hash ^ (char.codePointAt(0) ?? 0), 16_777_619);
  }
  return (hash >>> 0) / 4_294_967_296;
}

async function load(election: string, date: string) {
  const found = await db.execute<{
    contest: string;
    area: number | null;
    id: string | null;
    option: string;
    party: string | null;
    votes: number;
  }>(sql`
    with picked as (
      select distinct on (rs.contest_id) rs.contest_id, rs.k, l.scheme
      from contest c join result_set rs on rs.contest_id = c.id and rs.stage in ('official', 'certified')
        and rs.status = 'active' and rs.kind = 'counted'
      cross join lateral (
        select u.scheme_code as scheme from result_value v join reporting_unit u on u.k = v.unit_k
        where v.result_set_k = rs.k limit 1
      ) l
      where c.election_id = ${election}::uuid
      order by rs.contest_id, array_position(array['us.county', 'us.county_part'], l.scheme) nulls last, rs.is_default desc, rs.k
    )
    select p.contest_id as contest, a.k as area, a.id, coalesce(o.parent_option_id, o.id) as option, o.party_id as party,
      sum(v.value)::float8 as votes
    from picked p join result_value v on v.result_set_k = p.k and v.measure = 1
    join contest_option o on o.k = v.option_k
    join reporting_unit u on u.k = v.unit_k
    left join area a on a.id = u.area_id and p.scheme in ('us.county', 'us.county_part')
    group by 1, 2, 3, 4, 5
  `);
  const parties = await names(
    [
      ...new Set(
        found.rows.flatMap((row) => (row.party == null ? [] : [row.party])),
      ),
    ],
    date,
  );
  const size = new Map<string, number>();
  const largest = new Map<string, number>();
  for (const row of found.rows) {
    const key = `${row.contest}:${row.area ?? ""}`;
    const total = (size.get(key) ?? 0) + row.votes;
    size.set(key, total);
    largest.set(row.contest, Math.max(largest.get(row.contest) ?? 0, total));
  }
  return found.rows.map((row) => {
    const seed = String(row.area ?? row.contest);
    const scale =
      row.area == null
        ? 0.5
        : Math.log10(1 + (size.get(`${row.contest}:${row.area}`) ?? 0)) /
          Math.log10(1 + (largest.get(row.contest) ?? 1));
    const isLate = unit(`${seed}:late`) > 0.9;
    const start = isLate ? 4 : Math.floor(unit(`${seed}:start`) * 3);
    const end = Math.min(
      NIGHT - 2,
      start +
        3 +
        Math.round(14 * (0.4 * unit(`${seed}:end`) + 0.6 * Math.max(0, scale))),
    );
    const early = 0.3 + 0.3 * unit(`${seed}:early`);
    const party = parties.full.get(row.party ?? "") ?? "";
    const tilt = /democrat/iu.test(party)
      ? 1.3
      : /republican/iu.test(party)
        ? 0.75
        : 1;
    return {
      contest: row.contest,
      area: row.area,
      id: row.id,
      option: row.option,
      votes: row.votes,
      start,
      end,
      early: early * tilt,
      day: 1 - early * tilt,
      kept: 1 - 0.03 * unit(`${seed}:kept`),
    };
  });
}

export async function rehearsal(election: string, date: string) {
  let rows = loaded.get(election);
  if (rows == null) {
    rows = load(election, date);
    loaded.set(election, rows);
  }
  let ready: Awaited<typeof rows>;
  try {
    ready = await rows;
  } catch (error) {
    loaded.delete(election);
    throw error;
  }
  return (index: number) =>
    ready.map((row) => ({
      contest: row.contest,
      area: row.area,
      id: row.id,
      option: row.option,
      full: row.votes,
      votes:
        index < row.start
          ? 0
          : Math.round(
              row.votes *
                row.kept *
                (row.early +
                  row.day *
                    Math.min(1, (index - row.start) / (row.end - row.start))),
            ),
    }));
}
