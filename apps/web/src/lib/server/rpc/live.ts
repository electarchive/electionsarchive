import type { SQL } from "drizzle-orm";
import { openapi } from "@orpc/openapi";
import { ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { plane } from "#lib/server/plane.js";
import { names, UNAFFILIATED } from "#lib/server/rpc/archive.js";

const LEVELS = new Set([
  "us.place",
  "us.state",
  "us.county",
  "us.nyc_ad",
  "us.nyc_ed",
]);
const WHOLE = ["us.place", "us.state", "us.county"];
const ZONES: Record<string, string> = {
  ct: "America/New_York",
  de: "America/New_York",
  dc: "America/New_York",
  fl: "America/New_York",
  ga: "America/New_York",
  in: "America/New_York",
  ky: "America/New_York",
  me: "America/New_York",
  md: "America/New_York",
  ma: "America/New_York",
  mi: "America/New_York",
  nh: "America/New_York",
  nj: "America/New_York",
  ny: "America/New_York",
  nc: "America/New_York",
  oh: "America/New_York",
  pa: "America/New_York",
  ri: "America/New_York",
  sc: "America/New_York",
  vt: "America/New_York",
  va: "America/New_York",
  wv: "America/New_York",
  al: "America/Chicago",
  ar: "America/Chicago",
  il: "America/Chicago",
  ia: "America/Chicago",
  ks: "America/Chicago",
  la: "America/Chicago",
  mn: "America/Chicago",
  ms: "America/Chicago",
  mo: "America/Chicago",
  ne: "America/Chicago",
  nd: "America/Chicago",
  ok: "America/Chicago",
  sd: "America/Chicago",
  tn: "America/Chicago",
  tx: "America/Chicago",
  wi: "America/Chicago",
  co: "America/Denver",
  id: "America/Denver",
  mt: "America/Denver",
  nm: "America/Denver",
  ut: "America/Denver",
  wy: "America/Denver",
  az: "America/Phoenix",
  ca: "America/Los_Angeles",
  nv: "America/Los_Angeles",
  or: "America/Los_Angeles",
  wa: "America/Los_Angeles",
  ak: "America/Anchorage",
  hi: "Pacific/Honolulu",
};

const color = (party: SQL, date: SQL) => sql`
  coalesce(
    (select pc.color from party_color pc where pc.party_id = ${party} and pc.valid @> ${date}),
    (select pc.color from party_affiliation a join party_color pc on pc.party_id = a.parent_party_id and pc.valid @> ${date}
      where a.party_id = ${party} and a.kind = 'regional_branch' and a.valid @> ${date} limit 1)
  )
`;

function group(path: string) {
  const seat = path.split("/").at(-1) ?? "";
  if (
    /^(?:mayor|public-advocate|comptroller|governor|attorney-general)$/u.test(
      seat,
    )
  ) {
    return "executive";
  }
  if (seat.startsWith("borough-president")) {
    return "borough";
  }
  if (seat.startsWith("district-attorney")) {
    return "prosecutor";
  }
  if (seat.startsWith("council-district")) {
    return "council";
  }
  if (/^(?:proposal|proposition|question|measure)/u.test(seat)) {
    return "measure";
  }
  if (/court|surrogate|justice|judge/u.test(seat)) {
    return "judicial";
  }
  return "legislative";
}

async function election(path: string) {
  const segments = path.split("/").filter((part) => part !== "");
  const found = await db.execute<{
    id: string;
    path: string;
    date: string;
    polity: string;
  }>(sql`
    select e.id, s.path, e.date_from::text as date, e.polity_id as polity
    from slug s join election e on e.id = s.entity_id
    where s.is_current and s.path = any(${sql.param(segments.map((_, index) => segments.slice(0, index + 1).join("/")))}::text[])
    order by length(s.path) desc limit 1
  `);
  const row = found.rows[0];
  if (row == null) {
    throw new ORPCError("NOT_FOUND");
  }
  return { ...row, rest: segments.slice(row.path.split("/").length).join("/") };
}

async function sets(id: string) {
  const found = await db.execute<{
    k: number;
    contest: string;
    at: string;
    outside: boolean;
    scheme: string;
    note: string | null;
    publisher: string;
    url: string | null;
  }>(sql`
    select rs.k, rs.contest_id as contest, to_char(rs.as_of at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as at, c.election_id <> ${id}::uuid as outside,
      (select u.scheme_code from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = rs.k limit 1) as scheme,
      (select n.body_md from note n where n.entity_id = rs.id and n.kind = 'methodology' limit 1) as note,
      s.publisher, s.url
    from contest c join result_set rs on rs.contest_id = c.id and rs.stage in ('live', 'preliminary') and rs.status = 'active'
    join source s on s.id = rs.source_id
    join election e on e.id = c.election_id
    where rs.as_of is not null and e.date_from = (select date_from from election where id = ${id}::uuid)
      and s.publisher in (
        select z.publisher from result_set x join contest y on y.id = x.contest_id join source z on z.id = x.source_id
        where y.election_id = ${id}::uuid and x.stage in ('live', 'preliminary') and x.as_of is not null
      )
  `);
  return found.rows.filter((row) => LEVELS.has(row.scheme));
}

async function totals(ks: Array<number>) {
  const found = await db.execute<{
    set: number;
    option: string;
    votes: number;
  }>(sql`
    select v.result_set_k as set, coalesce(o.parent_option_id, o.id) as option, sum(v.value)::float8 as votes
    from result_value v join contest_option o on o.k = v.option_k
    where v.result_set_k = any(${sql.param(ks)}::int[]) and v.measure = 1
    group by 1, 2
  `);
  return found.rows;
}

async function certified(contests: Array<string>) {
  const found = await db.execute<{
    contest: string;
    k: number;
    option: string;
    votes: number;
    elected: boolean;
  }>(sql`
    with picked as (
      select distinct on (rs.contest_id) rs.contest_id, rs.k, rs.id from result_set rs
      where rs.contest_id = any(${sql.param(contests)}::uuid[]) and rs.stage in ('official', 'certified')
        and rs.status = 'active' and rs.kind = 'counted'
      order by rs.contest_id, rs.is_default desc, rs.stage = 'certified' desc
    )
    select p.contest_id as contest, p.k, t.option, t.votes,
      exists (select from outcome x where x.result_set_id = p.id and x.option_id = t.option and x.kind in ('elected', 'won')) as elected
    from picked p cross join lateral (
      select coalesce(o.parent_option_id, o.id) as option, sum(v.value)::float8 as votes
      from result_value v join contest_option o on o.k = v.option_k
      where v.result_set_k = p.k and v.measure = 1
      group by 1
    ) t
  `);
  return found.rows;
}

async function options(contests: Array<string>, date: string) {
  const found = await db.execute<{
    id: string;
    contest: string;
    kind: string;
    label: string | null;
    person: string | null;
    ballot: string | null;
    party: string | null;
    color: string | null;
  }>(sql`
    select o.id, o.contest_id as contest, o.kind::text, o.ballot_label as label, ca.person_id as person, ca.ballot_name as ballot,
      line.party, ${color(sql`line.party`, sql`${date}::date`)} as color
    from contest_option o left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
    left join lateral (
      select x.party_id as party from contest_option x
      where (x.id = o.id or x.parent_option_id = o.id) and x.party_id is not null
      order by (
        select max(v.value) from result_set rs join result_value v on v.result_set_k = rs.k and v.option_k = x.k
        where rs.contest_id = x.contest_id
      ) desc nulls last,
        exists (select from party_color pc where pc.party_id = x.party_id) desc, x.k
      limit 1
    ) line on true
    where o.contest_id = any(${sql.param(contests)}::uuid[]) and o.parent_option_id is null
  `);
  const people = found.rows.flatMap((row) =>
    row.person == null ? [] : [row.person],
  );
  const parties = found.rows.flatMap((row) =>
    row.party == null ? [] : [row.party],
  );
  const [named, partyNames] = await Promise.all([
    names(people, date),
    names(parties, date),
  ]);
  return found.rows.map((row) => ({
    id: row.id,
    contest: row.contest,
    kind: row.kind,
    name:
      (row.person == null ? null : named.full.get(row.person)) ??
      row.ballot ??
      row.label ??
      "",
    party:
      row.party == null
        ? null
        : (partyNames.short.get(row.party) ??
          partyNames.full.get(row.party) ??
          null),
    key:
      row.party == null
        ? `${row.kind}:${(row.label ?? "").toLowerCase()}`
        : `party:${row.party}`,
    color: row.color,
  }));
}

function scanners(note: string | null) {
  const list =
    /Share of scanners reported at this time: (.+)\.$/u.exec(note ?? "")?.[1] ??
    "";
  return list.split(", ").flatMap((part) => {
    const found = /^(.+) ([\d.]+)%$/u.exec(part);
    return found == null
      ? []
      : [{ area: found[1] ?? "", share: Number(found[2]) }];
  });
}

const Input = v.object({ path: v.string() });

export const live = {
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/live",
        summary:
          "An election night as the count came in: every update the official source published, each race's running totals by option at each update, the share of scanners reported where the source prints it, and the certified result once there is one",
      }),
    )
    .input(Input)
    .handler(async ({ input }) => {
      const found = await election(input.path);
      const stamped = await sets(found.id);
      if (stamped.length === 0) {
        throw new ORPCError("NOT_FOUND");
      }
      const bucket = new Map<string, string>();
      let end = "";
      const stamps = [...new Set(stamped.map((row) => row.at))].toSorted(
        (a, b) => b.localeCompare(a),
      );
      for (const at of stamps) {
        if (end === "" || Date.parse(end) - Date.parse(at) > 120_000) {
          end = at;
        }
        bucket.set(at, end);
      }
      const listed = stamped
        .map((row) => ({
          ...row,
          stamp: row.at,
          at: bucket.get(row.at) ?? row.at,
        }))
        .toSorted((a, b) => a.stamp.localeCompare(b.stamp));
      const contests = [...new Set(listed.map((row) => row.contest))];
      const [summed, settled, choices, titles, slugs] = await Promise.all([
        totals(listed.map((row) => row.k)),
        certified(contests),
        options(contests, found.date),
        names([found.id, found.polity, ...contests], found.date),
        db.execute<{ id: string; path: string }>(sql`
          select entity_id as id, path from slug where is_current and entity_id = any(${sql.param(contests)}::uuid[])
        `),
      ]);
      const paths = new Map(slugs.rows.map((row) => [row.id, row.path]));
      const bySet = Map.groupBy(summed, (row) => row.set);
      const moments = [...new Set(listed.map((row) => row.at))].toSorted(
        (a, b) => a.localeCompare(b),
      );
      const frames = moments.map((at) => ({
        at,
        scanners: [
          ...new Map(
            listed
              .filter((row) => row.at === at)
              .flatMap((row) => scanners(row.note))
              .map((row) => [row.area, row.share]),
          ),
        ].map(([area, share]) => ({ area, share })),
      }));
      const races = contests.map((contest) => {
        const own = choices.filter((choice) => choice.contest === contest);
        const index = new Map(
          own.map((choice, position) => [choice.id, position]),
        );
        const mine = listed.filter((row) => row.contest === contest);
        const series = moments.flatMap((at) => {
          const chosen = mine
            .filter((row) => row.at === at && WHOLE.includes(row.scheme))
            .toSorted(
              (a, b) =>
                WHOLE.indexOf(a.scheme) - WHOLE.indexOf(b.scheme) ||
                b.stamp.localeCompare(a.stamp),
            )[0];
          if (chosen == null) {
            return [];
          }
          const votes = own.map(() => 0);
          const rows = bySet.get(chosen.k) ?? [];
          for (const row of rows) {
            const position = index.get(row.option);
            if (position != null) {
              votes[position] = row.votes;
            }
          }
          return [{ at, level: chosen.scheme, votes }];
        });
        const final = settled.filter((row) => row.contest === contest);
        const votes = own.map(() => 0);
        for (const row of final) {
          const position = index.get(row.option);
          if (position != null) {
            votes[position] = row.votes;
          }
        }
        const path = paths.get(contest) ?? "";
        const tally = final.length > 0 ? votes : (series.at(-1)?.votes ?? []);
        const top = Math.max(0, ...tally);
        return {
          id: contest,
          path,
          name: (titles.full.get(contest) ?? path.split("/").at(-1) ?? "")
            .replace(/^\d{4} /u, "")
            .replace(/ election$/u, ""),
          group: group(path),
          options: own.map(({ id, name, party, key, color, kind }, at) => ({
            id,
            name,
            party,
            key,
            color:
              color ??
              (key.startsWith("party:") && (tally[at] ?? 0) >= top * 0.05
                ? (UNAFFILIATED[
                    Number.parseInt(key.slice(-4), 16) % UNAFFILIATED.length
                  ] ?? null)
                : null),
            kind,
          })),
          outside: mine.some((row) => row.outside),
          series,
          maps: mine
            .filter(
              (row) => row.scheme !== "us.place" && row.scheme !== "us.state",
            )
            .map((row) => ({ at: row.at, level: row.scheme }))
            .toSorted((a, b) => a.at.localeCompare(b.at)),
          certified:
            final.length === 0 || mine.some((row) => row.outside)
              ? null
              : {
                  votes,
                  elected: own.map((choice) =>
                    final.some(
                      (row) => row.option === choice.id && row.elected,
                    ),
                  ),
                },
        };
      });
      const source = listed[0];
      return {
        election: {
          id: found.id,
          path: found.path,
          name: titles.full.get(found.id) ?? found.path,
          place: titles.full.get(found.polity) ?? "",
          zone: ZONES[found.path.split("/", 2)[1] ?? ""] ?? "UTC",
          date: found.date,
        },
        focus: found.rest === "" ? null : `${found.path}/${found.rest}`,
        source: {
          publisher: source?.publisher ?? "",
          url: source?.url ?? null,
        },
        frames,
        races: races.filter((race) => race.series.length > 0),
      };
    }),
  map: os
    .input(
      v.object({
        contest: v.pipe(v.string(), v.uuid()),
        at: v.nullable(v.pipe(v.string(), v.isoTimestamp())),
        level: v.picklist(["us.nyc_ed", "us.nyc_ad", "us.county"]),
      }),
    )
    .handler(async ({ input }) => {
      const picked = await db.execute<{
        k: number;
        at: string | null;
        date: string;
      }>(sql`
        select rs.k, to_char(rs.as_of at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as at, e.date_from::text as date
        from result_set rs join election e on e.id = rs.election_id
        where rs.contest_id = ${input.contest}::uuid and rs.status = 'active'
          and (${input.at}::timestamptz is null and rs.is_default and rs.stage in ('official', 'certified')
            or rs.stage in ('live', 'preliminary') and rs.as_of <= ${input.at}::timestamptz)
          and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                      where v.result_set_k = rs.k and u.scheme_code = ${input.level} limit 1)
        order by rs.as_of desc limit 1
      `);
      const chosen = picked.rows[0];
      if (chosen == null) {
        return null;
      }
      const scheme = input.level === "us.nyc_ad" ? "us.sldl" : input.level;
      const [values, tiles] = await Promise.all([
        db.execute<{
          area: number;
          id: string;
          option: string;
          votes: number;
        }>(sql`
          select a.k as area, a.id, coalesce(o.parent_option_id, o.id) as option, sum(v.value)::float8 as votes
          from result_value v join reporting_unit u on u.k = v.unit_k and u.scheme_code = ${input.level}
          join area a on a.id = u.area_id
          join contest_option o on o.k = v.option_k
          where v.result_set_k = ${chosen.k} and v.measure = 1
          group by 1, 2, 3
        `),
        db.execute<{
          tiles: string;
          west: number;
          south: number;
          east: number;
          north: number;
        }>(sql`
          select coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) as tiles,
            st_xmin(x.box) as west, st_ymin(x.box) as south, st_xmax(x.box) as east, st_ymax(x.box) as north
          from boundary_set bs, lateral (
            select ST_Transform(ST_SetSRID(ST_Extent(${plane("g.geom", "a.scheme_code")})::geometry, 3857), 4326) as box
            from area_geometry g join area a on a.id = g.area_id
            where g.area_id in (select u.area_id from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = ${chosen.k})
              and g.valid @> ${chosen.date}::date
          ) x
          where bs.scheme_code = ${scheme} and bs.valid @> ${chosen.date}::date
          order by bs.valid_from desc nulls last limit 1
        `),
      ]);
      const frame = tiles.rows[0];
      if (frame == null) {
        return null;
      }
      const titles = await names(
        [...new Set(values.rows.map((row) => row.id))],
        chosen.date,
      );
      const areas = Map.groupBy(values.rows, (row) => row.area);
      const options = [...new Set(values.rows.map((row) => row.option))];
      const index = new Map(
        options.map((option, position) => [option, position]),
      );
      return {
        at: chosen.at,
        tiles: frame.tiles,
        frame: {
          west: frame.west,
          south: frame.south,
          east: frame.east,
          north: frame.north,
        },
        options,
        units: [...areas].map(([area, rows]) => {
          const votes = options.map(() => 0);
          for (const row of rows) {
            votes[index.get(row.option) ?? 0] = row.votes;
          }
          return {
            k: area,
            name: titles.full.get(rows[0]?.id ?? "") ?? "",
            votes,
          };
        }),
      };
    }),
};
