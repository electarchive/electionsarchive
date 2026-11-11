import { getRequestEvent } from "$app/server";
import { sql } from "drizzle-orm";
import { roles } from "#lib/server/contrib.js";
import { liveDb as db } from "#lib/server/db/index.js";
import { names } from "#lib/server/rpc/archive.js";

const UTC = sql.raw(`'YYYY-MM-DD"T"HH24:MI:SS"Z"'`);

const UNCERTIFIED = sql`not exists (
  select from result_set rs where rs.contest_id = c.id and rs.status = 'active' and rs.kind = 'counted'
    and rs.stage in ('official', 'certified', 'court_revised', 'recount')
)`;

const LIVE_ELECTION = /^us\/(?:[a-z]{2}\/)?\d{4}-\d{2}-\d{2}-general$/u;

export type Tally = {
  at: string;
  publisher: string;
  url: string | null;
  reporting: number | null;
  expected: number | null;
  rows: Array<{ label: string; party: string | null; votes: number }>;
};

export type Counted = {
  path: string;
  contests: number;
  at: string | null;
  tally: Tally | null;
};

async function shown() {
  const [found, role] = await Promise.all([
    db.execute<{ isShown: boolean }>(sql`
      select coalesce((select shown from live.switch where name = 'results'), false) as "isShown"
    `),
    (async () => {
      try {
        return await roles(getRequestEvent().locals.user?.id);
      } catch {
        return { admin: false, reviewer: false };
      }
    })(),
  ]);
  return { isShown: found.rows[0]?.isShown ?? false, isAdmin: role.admin };
}

async function tally(contest: string, date: string): Promise<Tally | null> {
  const found = await db.execute<{
    k: number;
    at: string;
    publisher: string;
    url: string | null;
    reporting: number | null;
    expected: number | null;
    option: string;
    votes: number;
  }>(sql`
    with latest as (
      select distinct on (f.feed_id) f.k, f.as_of, f.reporting, f.expected, d.publisher, d.url
      from live.frame f join live.feed d on d.id = f.feed_id and d.enabled
      where f.contest_id = ${contest}::uuid and f.status = 'active'
      order by f.feed_id, f.as_of desc, f.k desc
    )
    select l.k, to_char(l.as_of at time zone 'UTC', ${UTC}) as at, l.publisher, l.url, l.reporting, l.expected,
      coalesce(o.parent_option_id, o.id) as option, sum(v.votes)::float8 as votes
    from latest l join live.value v on v.frame_k = l.k join contest_option o on o.id = v.option_id
    group by l.k, l.as_of, l.publisher, l.url, l.reporting, l.expected, 7
  `);
  const frames = Map.groupBy(found.rows, (row) => row.k);
  let best: Array<(typeof found.rows)[number]> = [];
  let most = -1;
  for (const rows of frames.values()) {
    let total = 0;
    for (const row of rows) {
      total += row.votes;
    }
    if (
      total > most ||
      (total === most && (rows[0]?.at ?? "") > (best[0]?.at ?? ""))
    ) {
      best = rows;
      most = total;
    }
  }
  const head = best[0];
  if (head == null) {
    return null;
  }
  const labeled = await db.execute<{
    id: string;
    label: string | null;
    person: string | null;
    ballot: string | null;
    party: string | null;
  }>(sql`
    select o.id, o.ballot_label as label, ca.person_id as person, ca.ballot_name as ballot,
      (select x.party_id from contest_option x where (x.id = o.id or x.parent_option_id = o.id) and x.party_id is not null order by x.k limit 1) as party
    from contest_option o left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
    where o.id = any(${sql.param(best.map((row) => row.option))}::uuid[])
  `);
  const people = labeled.rows.flatMap((row) => row.person ?? []);
  const parties = labeled.rows.flatMap((row) => row.party ?? []);
  const [named, partyNames] = await Promise.all([
    names(people, date),
    names(parties, date),
  ]);
  const byId = new Map(labeled.rows.map((row) => [row.id, row]));
  return {
    at: head.at,
    publisher: head.publisher,
    url: head.url,
    reporting: head.reporting,
    expected: head.expected,
    rows: best
      .map((row) => {
        const option = byId.get(row.option);
        return {
          label:
            (option?.person == null ? null : named.full.get(option.person)) ??
            option?.ballot ??
            option?.label ??
            "",
          party:
            option?.party == null
              ? null
              : (partyNames.short.get(option.party) ??
                partyNames.full.get(option.party) ??
                null),
          votes: row.votes,
        };
      })
      .toSorted((a, b) => b.votes - a.votes),
  };
}

export async function countedContest(
  id: string,
  path: string,
): Promise<Counted | null> {
  const found = await db.execute<{ date: string }>(sql`
    select e.date_from::text as date from contest c join election e on e.id = c.election_id
    where c.id = ${id}::uuid and ${UNCERTIFIED}
      and exists (
        select from live.frame f join live.feed d on d.id = f.feed_id and d.enabled
        where f.contest_id = c.id and f.status = 'active'
      )
  `);
  const row = found.rows[0];
  if (row == null) {
    return null;
  }
  const { isShown, isAdmin } = await shown();
  if (!isShown && !isAdmin) {
    return null;
  }
  const counted = isShown ? await tally(id, row.date) : null;
  return {
    path: `live/${path}`,
    contests: 1,
    at: counted?.at ?? null,
    tally: counted,
  };
}

const FRESH_MS = 60_000;

let held: { at: number; value: Promise<Map<string, Lit>> } | null = null;
let last: Map<string, Lit> | null = null;

type Lit = { contests: number; at: string };

async function load() {
  const found = await db.execute<Lit & { election: string }>(sql`
    with recursive lit as materialized (
      select f.contest_id as id, max(f.as_of) as at
      from live.frame f join live.feed d on d.id = f.feed_id and d.enabled
      where f.status = 'active' group by 1
    ),
    held as (
      select c.election_id, l.id, l.at from lit l join contest c on c.id = l.id where ${UNCERTIFIED}
    ),
    up(election, contest, at) as (
      select election_id, id, at from held
      union all
      select e.parent_id, up.contest, up.at from up join election e on e.id = up.election where e.parent_id is not null
    )
    select election, count(distinct contest)::int as contests, to_char(max(at) at time zone 'UTC', ${UTC}) as at
    from up group by 1
  `);
  return new Map(
    found.rows.map((row) => [
      row.election,
      { contests: row.contests, at: row.at },
    ]),
  );
}

async function lit() {
  if (held == null || Date.now() - held.at > FRESH_MS) {
    const value = load();
    held = { at: Date.now(), value };
    value
      .then((loaded) => {
        last = loaded;
      })
      .catch(() => {
        if (held?.value === value) {
          held = null;
        }
      });
  }
  return last ?? held.value;
}

export async function countedElection(id: string): Promise<Counted | null> {
  const found = (await lit()).get(id);
  if (found == null) {
    return null;
  }
  const [{ isShown, isAdmin }, ancestors] = await Promise.all([
    shown(),
    db.execute<{ path: string }>(sql`
      with recursive up as (
        select id, parent_id, 0 as depth from election where id = ${id}::uuid
        union all
        select e.id, e.parent_id, u.depth + 1 from election e join up u on u.parent_id = e.id where u.depth < 4
      )
      select s.path from up u join slug s on s.entity_id = u.id and s.is_current order by u.depth
    `),
  ]);
  const path = ancestors.rows.find((row) => LIVE_ELECTION.test(row.path));
  if (path == null || (!isShown && !isAdmin)) {
    return null;
  }
  return {
    path: `live/${path.path}`,
    contests: isShown ? found.contests : 0,
    at: isShown ? found.at : null,
    tally: null,
  };
}
