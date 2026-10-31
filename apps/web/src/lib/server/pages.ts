import { call, ORPCError } from "@orpc/server";
import { error, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import type { Prose } from "#lib/contrib.js";
import { effective, stand } from "#lib/caucus.js";
import { headline } from "#lib/coverage.js";
import { era } from "#lib/party.js";
import { db } from "#lib/server/db/index.js";
import { logos, partiesIn } from "#lib/server/logos.js";
import { proseColumns, proseJoin } from "#lib/server/prose.js";
import {
  eras,
  latest,
  representative,
  separate,
} from "#lib/server/rpc/archive.js";
import { router } from "#lib/server/rpc/router.js";
import land from "#lib/server/world.txt?raw";

async function found<T>(promise: Promise<T>) {
  try {
    return await promise;
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
}

async function draw() {
  const result = await db.execute<{
    id: string;
    path: string;
    covered: boolean;
    box: string;
    inset: string;
  }>(
    sql`
      with shapes as (
        select distinct on (pt.polity_id) pt.polity_id as id, ag.geom
        from polity_territory pt join polity y on y.id = pt.polity_id
        join area_geometry ag on ag.area_id = pt.area_id
        where y.kind = 'sovereign_state' and upper_inf(pt.valid)
          and exists (select from election e where e.polity_id = y.id)
        order by pt.polity_id, upper(ag.valid) desc nulls first, lower(ag.valid) desc nulls last
      ),
      context as (
        select distinct on (a.id) a.id, ag.geom
        from area a join area_geometry ag on ag.area_id = a.id
        where a.scheme_code = 'eu.member_state'
        order by a.id, upper(ag.valid) desc nulls first
      ),
      projected as (
        select id, ST_Scale(ST_Transform(geom, 8857), 0.001, 0.001) as geom, true as covered from shapes
        union all
        select id, ST_Scale(ST_Transform(geom, 8857), 0.001, 0.001), false from context
      ),
      cleaned as (
        select id, covered, ST_SimplifyPreserveTopology(ST_Collect(d.geom), 12) as geom
        from projected cross join lateral ST_Dump(projected.geom) d
        where ST_Area(d.geom) > 600
        group by id, covered
      ),
      bounds as (
        select ST_Extent(geom) as box,
          ST_Scale(ST_Transform(ST_Segmentize(ST_MakeEnvelope(-11, 35, 45, 61, 4326), 1), 8857), 0.001, 0.001) as europe
        from cleaned
      )
      select c.id, ST_AsSVG(c.geom, 0, 0) as path, c.covered,
        concat_ws(' ', floor(ST_XMin(b.box)) - 40, -ceil(ST_YMax(b.box)) - 40,
          ceil(ST_XMax(b.box) - ST_XMin(b.box)) + 80, ceil(ST_YMax(b.box) - ST_YMin(b.box)) + 80) as box,
        concat_ws(' ', floor(ST_XMin(b.europe)), -ceil(ST_YMax(b.europe)),
          ceil(ST_XMax(b.europe) - ST_XMin(b.europe)), ceil(ST_YMax(b.europe) - ST_YMin(b.europe))) as inset
      from cleaned c cross join bounds b
      order by c.covered, c.id
    `,
  );
  return {
    box: result.rows[0]?.box ?? "0 0 1 1",
    inset: result.rows[0]?.inset ?? "0 0 1 1",
    land,
    shapes: result.rows.map((row) => ({
      id: row.id,
      path: row.path,
      isCovered: row.covered,
    })),
  };
}

const atlas = new Map<string, ReturnType<typeof draw>>();

export async function home() {
  const drawn = atlas.get("world") ?? draw();
  atlas.set("world", drawn);
  const [polities, shapes, dated, held] = await Promise.all([
    call(router.polities.list, { kinds: ["sovereign_state", "supranational"] }),
    drawn,
    db.execute<{
      id: string;
      name: string | null;
      path: string;
      date: string;
      kind: string;
      polity: string | null;
      when: string;
    }>(sql`
      with picked as (
        (select e.id, e.date_from, e.kind, e.polity_id, 'recent' as when
          from election e join polity y on y.id = e.polity_id
          where e.parent_id is null and y.kind in ('sovereign_state', 'supranational')
            and e.kind in ('general', 'referendum') and e.date_from <= current_date
            and (
              exists (select from contest c where c.election_id = e.id)
              or (y.kind = 'sovereign_state' and exists (
                select from election x join contest c on c.election_id = x.id where x.parent_id = e.id
              ))
            )
          order by e.date_from desc limit 6)
        union all
        (select e.id, e.date_from, e.kind, e.polity_id, 'upcoming'
          from election e
          where e.parent_id is null and e.date_from > current_date and not exists (select from entity_merge m where m.entity_id = e.id)
          order by e.date_from limit 4)
        union all
        (select t.id, t.date_from, t.kind, t.polity_id, 'today'
          from (
            select distinct on (case when y.kind in ('sovereign_state', 'supranational') then e.id else e.polity_id end)
              e.id, e.date_from, e.kind, e.polity_id,
              y.kind in ('sovereign_state', 'supranational') as is_national
            from election e join polity y on y.id = e.polity_id
            cross join lateral (
              select abs(extract(doy from e.date_from) - extract(doy from current_date)) as gap
            ) d
            where e.parent_id is null and e.date_from < current_date - 300 and not exists (select from entity_merge m where m.entity_id = e.id)
              and least(d.gap, 365 - d.gap) <= 3
            order by case when y.kind in ('sovereign_state', 'supranational') then e.id else e.polity_id end,
              (e.kind = 'general') desc, e.date_from desc
          ) t
          order by t.kind in ('general', 'referendum') desc, t.is_national desc, t.date_from desc limit 8)
      )
      select p.id, p.date_from::text as date, p.kind::text as kind, p.when, s.path,
        (select n.text from name n where n.entity_id = p.id and n.kind in ('official', 'english')
          order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc, n.recorded_from desc, n.id limit 1) as name,
        (select n.text from name n
          where n.entity_id = coalesce((
            with recursive up(id) as (
              select p.polity_id
              union
              select pp.parent_id from polity_parent pp join up on pp.polity_id = up.id
            )
            select up.id from up join polity q on q.id = up.id where q.kind = 'sovereign_state' limit 1
          ), p.polity_id) and n.kind in ('official', 'english')
          order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc, n.recorded_from desc, n.id limit 1) as polity
      from picked p join slug s on s.entity_id = p.id and s.is_current
    `),
    db.execute<{ id: string; years: Array<number> }>(sql`
      select e.polity_id as id,
        array_agg(distinct extract(year from e.date_from)::int) as years
      from election e join polity y on y.id = e.polity_id
      where e.parent_id is null and y.kind in ('sovereign_state', 'supranational')
        and e.kind = 'general' and e.date_from <= current_date and not exists (select from entity_merge m where m.entity_id = e.id)
      group by e.polity_id
    `),
  ]);
  const years = new Map(held.rows.map((row) => [row.id, row.years]));
  const covered = polities
    .filter(
      (polity) =>
        polity.kind === "sovereign_state" || polity.kind === "supranational",
    )
    .map((polity) => ({ ...polity, years: years.get(polity.id) ?? [] }));
  const elections = dated.rows.map((row) => ({
    id: row.id,
    name: row.name ?? "",
    path: row.path,
    date: row.date,
    kind: row.kind,
    polity: row.polity,
    when: row.when,
  }));
  const recent = elections.filter((election) => election.when === "recent");
  const leads = await Promise.all(
    recent.map(async (election) =>
      call(router.elections.lead, { id: election.id }),
    ),
  );
  return {
    kind: "home" as const,
    polities: covered,
    atlas: shapes,
    recent: recent.map((election, at) => ({
      ...election,
      lead: leads[at] ?? null,
    })),
    upcoming: elections
      .filter((election) => election.when === "upcoming")
      .toSorted((a, b) => a.date.localeCompare(b.date)),
    today: elections.filter((election) => election.when === "today"),
  };
}

export async function coverage(polity: string) {
  if (!/^[a-z]{2}$/.test(polity)) {
    error(404);
  }
  const goals = await found(call(router.coverage.goals, { polity }));
  const isFlat =
    polity !== "us" && Object.keys(goals.summary.regions).length === 0;
  const gaps = Object.fromEntries(
    await Promise.all(
      goals.tiers.map(
        async (tier) =>
          [
            tier.key,
            await call(router.coverage.gaps, {
              polity,
              goal: headline(polity, tier.key).goal,
              tier: tier.key,
              state: isFlat ? "" : undefined,
            }),
          ] as const,
      ),
    ),
  );
  return { kind: "coverage" as const, polity, isFlat, ...goals, gaps };
}

export async function coverageOverview() {
  return {
    kind: "coverage_overview" as const,
    ...(await call(router.coverage.overview)),
  };
}

export async function person(slug: string) {
  const { id } = await found(call(router.people.locate, { slug }));
  const [record, parties] = await Promise.all([
    call(router.people.get, { id }),
    call(router.people.parties, { id }),
  ]);
  return { kind: "person" as const, id, person: record, parties };
}

export async function party(path: string, place: string | null = null) {
  const segments = path.split("/");
  const [{ id }, scope] = await Promise.all([
    found(
      call(router.parties.locate, {
        polity:
          segments.length > 1 ? segments.slice(0, -1).join("/") : undefined,
        slug: segments.at(-1) ?? "",
      }),
    ),
    place == null
      ? null
      : db.execute<{ id: string }>(sql`
        select s.entity_id as id from slug s join polity p on p.id = s.entity_id
        where s.path = ${place} and s.is_current
      `),
  ]);
  const within = scope?.rows[0]?.id;
  if (scope != null && within == null) {
    error(404);
  }
  if (
    within != null &&
    (
      await db.execute<Record<string, never>>(
        sql`select from party where id = ${id}::uuid and polity_id = ${within}::uuid`,
      )
    ).rows.length > 0
  ) {
    redirect(308, `/parties/${path}`);
  }
  const [page, generals, members, notes] = await Promise.all([
    (async () => {
      if (within == null) {
        const stored = await db.execute<{
          page: Awaited<ReturnType<typeof contents>>;
        }>(sql`select page from party_page where party_id = ${id}::uuid`);
        const found = stored.rows[0]?.page;
        if (found != null) {
          return found.party.events == null
            ? { ...found, party: await call(router.parties.get, { id }) }
            : found;
        }
      }
      return contents(id, within);
    })(),
    db.execute<{ id: string; date: string }>(sql`
      with ids as (
        select ${id}::uuid as id
        union
        select entity_id from entity_merge where into_id = ${id}::uuid
        union
        select party_id from party_affiliation where kind = 'regional_branch' and parent_party_id = ${id}::uuid
      )
      select e.id, e.date_from::text as date from election e
      where e.kind = 'general' and e.date_from <= current_date
        and case when ${within ?? null}::uuid is null
          then e.parent_id is null and e.polity_id = (select polity_id from party where id = ${id}::uuid)
          else e.polity_id = ${within ?? null}::uuid end
        and exists (
          select from election x join contest c on c.election_id = x.id
          join contest_option o on o.contest_id = c.id
          where (x.id = e.id or x.parent_id = e.id) and o.party_id in (select id from ids)
        )
      order by e.date_from desc
    `),
    db.execute<{
      person: string;
      name: string;
      from: string | null;
      to: string | null;
    }>(sql`
      select coalesce(pm.into_id, m.person_id) as person, n.text as name,
        m.valid_from::text as from, m.valid_to::text as to
      from person_party_membership m join party k on k.id = m.party_id and k.kind = 'caucus'
      left join entity_merge pm on pm.entity_id = m.person_id
      cross join lateral (
        select n.text from name n where n.entity_id = coalesce(pm.into_id, m.person_id)
        order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc, n.recorded_from desc, n.id limit 1
      ) n
      where m.party_id = ${id}::uuid or m.party_id in (select entity_id from entity_merge where into_id = ${id}::uuid)
      order by m.valid_from nulls first, n.text
    `),
    db.execute<Prose & { from: string | null; to: string | null }>(sql`
      select ${proseColumns}, n.valid_from::text as from, n.valid_to::text as to
      from note n
      ${proseJoin("n")}
      where n.kind = 'caveat'
        and (n.entity_id = ${id}::uuid or n.entity_id in (select entity_id from entity_merge where into_id = ${id}::uuid))
      order by n.valid_from nulls first
    `),
  ]);
  const home = page.party.polity?.path ?? null;
  const elsewhere = [
    ...new Set(
      page.record.elections.flatMap((election) => {
        const found = /^([a-z]{2})\/\d{4}-\d{2}-\d{2}-general$/u.exec(
          election.path ?? "",
        )?.[1];
        return within == null && found != null && found !== home ? [found] : [];
      }),
    ),
  ];
  const abroad =
    elsewhere.length === 0
      ? []
      : (
          await db.execute<{ name: string; path: string }>(sql`
            select s.path, (
              select n.text from name n where n.entity_id = s.entity_id
              order by n.is_preferred desc, (n.lang = 'en') desc, n.recorded_from desc, n.id limit 1
            ) as name
            from slug s join polity p on p.id = s.entity_id and p.kind = 'sovereign_state'
            where s.is_current and s.path = any(${sql.param(elsewhere)}::text[])
          `)
        ).rows.toSorted(
          (a, b) => elsewhere.indexOf(a.path) - elsewhere.indexOf(b.path),
        );
  const [header, eras, related] = await Promise.all([
    logos([id], null),
    Promise.all(
      (page.party.names ?? [])
        .flatMap(({ name, short, to }) =>
          to == null ? [] : [{ name: short ?? name, to }],
        )
        .map(async ({ name, to }) => {
          const found = await logos(
            [id],
            new Date(Date.parse(to) - 86_400_000).toISOString().slice(0, 10),
            true,
          );
          return { name, logo: found[id] ?? null };
        }),
    ),
    Promise.all(
      page.party.relations.map(async (relation) =>
        logos(
          [relation.party.id],
          relation.span?.to ?? relation.date ?? null,
          true,
        ),
      ),
    ),
  ]);
  return {
    kind: "party" as const,
    id,
    ...page,
    logos: {
      header: header[id] ?? null,
      eras: eras.flatMap(({ name, logo }, index) =>
        logo == null ||
        eras.slice(0, index).some((other) => other.logo?.src === logo.src)
          ? []
          : [{ name, logo }],
      ),
      related: Object.fromEntries(
        related.flatMap((found) => Object.entries(found)),
      ),
    },
    elections: generals.rows,
    members: members.rows,
    notes: notes.rows,
    abroad,
  };
}

export async function contents(id: string, within?: string) {
  const [party, record, history] = await Promise.all([
    call(router.parties.get, { id, within }),
    call(router.parties.elections, { id, within }),
    call(router.parties.history, { id, within }),
  ]);
  return { party, record, history };
}

function total(parties: Array<{ seats: number }>) {
  let sum = 0;
  for (const entry of parties) {
    sum += entry.seats;
  }
  return sum;
}

export async function chamber(path: string) {
  const segments = path.split("/");
  const { id, polity } = await found(
    call(router.bodies.locate, {
      polity: segments.slice(0, -1).join("/"),
      slug: segments.at(-1) ?? "",
    }),
  );
  const [body, record, council] = await Promise.all([
    call(router.bodies.get, { id }),
    call(router.polities.record, { id: polity, body: id }),
    call(router.seats.council, { id }),
  ]);
  const size = (on: string) =>
    (
      body.sizes.find(
        (entry) =>
          entry.from != null &&
          entry.from >= on &&
          Date.parse(entry.from) - Date.parse(on) < 3.5e10,
      ) ??
      body.sizes.find(
        (entry) =>
          (entry.from == null || entry.from <= on) &&
          (entry.to == null || entry.to > on),
      )
    )?.seats ?? null;
  const filled = (record.series[0]?.points ?? []).map((point) => ({
    point,
    seats: total(point.parties),
  }));
  let most = 0;
  for (const entry of filled) {
    most = Math.max(most, entry.seats);
  }
  const points = filled
    .filter((entry) => entry.seats >= 0.25 * (size(entry.point.date) ?? most))
    .map((entry) => entry.point);
  const covered = body.makeup.filter((term) => {
    const seats = size(term.start);
    return seats == null || total(term.parties) >= 0.85 * seats;
  });
  const isSeated =
    body.makeup.length > 0 && covered.length >= 0.8 * body.makeup.length;
  const results = points.map((point) => ({
    start: point.date,
    parties: point.parties,
    election: { name: point.name, path: point.path, date: point.date },
    isSeated: false,
    apportioned: null,
    seceded: 0,
    undated: [] as Array<string | null>,
  }));
  const first = Date.parse(body.makeup[0]?.start ?? "9999-12-31") - 2e10;
  const earlier = results.filter((term) => Date.parse(term.start) < first);
  const final = Date.parse(body.makeup.at(-1)?.start ?? "0000-01-01") + 2e10;
  const later = results.filter((term) => Date.parse(term.start) > final);
  const raw = isSeated
    ? [
        ...earlier,
        ...body.makeup.map((term) => {
          const point = points.findLast((entry) => entry.date <= term.start);
          return {
            start: term.start,
            parties: term.parties,
            isSeated: true,
            apportioned: term.apportioned,
            seceded: term.seceded,
            undated: term.undated,
            election:
              point == null ||
              Date.parse(term.start) - Date.parse(point.date) > 8e10
                ? null
                : { name: point.name, path: point.path, date: point.date },
          };
        }),
        ...later,
      ]
    : results;
  const groups = Map.groupBy(
    [
      ...record.parties,
      ...body.parties.filter((party) =>
        record.parties.every((entry) => entry.id !== party.id),
      ),
    ],
    (party) => party.label.trim().toLocaleLowerCase(),
  )
    .values()
    .toArray();
  const shared = groups.flatMap((group) =>
    group.length > 1 ? group.map((party) => party.id) : [],
  );
  const naming =
    shared.length === 0
      ? null
      : await db.execute<{ id: string }>(sql`
        select distinct entity_id::text as id from name
        where entity_id = any(${sql.param(shared.filter((key) => /^[\da-f-]{36}$/u.test(key)))}::uuid[])
          and kind in ('official', 'english') and not (lower_inf(valid) and upper_inf(valid))
      `);
  const renamed = new Set((naming?.rows ?? []).map((row) => row.id));
  const kin = new Map(
    groups.flatMap((group) => {
      const root = group.find((party) => !renamed.has(party.id))?.id;
      return group.map(
        (party) =>
          [
            party.id,
            renamed.has(party.id) ? party.id : (root ?? party.id),
          ] as const,
      );
    }),
  );
  const rekey = (key: string | null) =>
    key == null || key === "independent" || key.startsWith("category:")
      ? key
      : key
          .split(">")
          .map((part) =>
            part === "independent" ? part : (kin.get(part) ?? part),
          )
          .join(">");
  const running = new Map<string | null, number>();
  const came = new Map<string, Map<string, number>>();
  const days = Map.groupBy(body.changes, (row) => row.date)
    .entries()
    .map(([day, rows]) => {
      const moves = rows.map((row) => ({
        ...row,
        from: rekey(row.from),
        to: rekey(row.to),
      }));
      for (const move of moves) {
        if (move.kind !== "in") {
          running.set(move.from, (running.get(move.from) ?? 0) - move.members);
        }
        if (move.kind !== "out") {
          running.set(move.to, (running.get(move.to) ?? 0) + move.members);
        }
        if (
          move.kind === "switch" &&
          move.from != null &&
          move.to != null &&
          move.from !== "independent" &&
          !move.from.startsWith("category:")
        ) {
          const tally = came.get(move.to) ?? new Map<string, number>();
          tally.set(move.from, (tally.get(move.from) ?? 0) + move.members);
          came.set(move.to, tally);
        }
      }
      return {
        date: day,
        parties: [...running]
          .filter(([, count]) => count > 0)
          .map(([party, count]) => ({ party, seats: count }))
          .toSorted((a, b) => b.seats - a.seats),
        near: Object.fromEntries(
          [...came]
            .filter(([party]) => (running.get(party) ?? 0) > 0)
            .map(([party, tally]) => [
              party,
              [...tally].toSorted((a, b) => b[1] - a[1])[0]?.[0] ?? null,
            ]),
        ),
        moves: moves
          .filter((move) => move.kind !== "rename")
          .map(({ kind, from, to, cause, members }) => ({
            kind,
            from,
            to,
            cause,
            members,
          })),
      };
    })
    .toArray();
  const gaps = raw
    .slice(1)
    .map(
      (term, index) =>
        Date.parse(term.start) - Date.parse(raw[index]?.start ?? term.start),
    )
    .toSorted((a, b) => a - b);
  const typical = gaps[Math.floor(gaps.length / 2)] ?? null;
  const partial: Array<boolean> = [];
  for (const [index, term] of raw.entries()) {
    const seats = total(term.parties);
    const known = size(term.start);
    const before = raw[index - 1];
    const near = raw
      .filter(
        (other) =>
          Math.abs(Date.parse(other.start) - Date.parse(term.start)) < 2.5e11,
      )
      .map((other) => total(other.parties));
    partial.push(
      !term.isSeated &&
        (known == null
          ? seats < 0.6 * Math.max(...near) ||
            (before != null &&
              partial[index - 1] === true &&
              Math.abs(seats - total(before.parties)) <=
                0.2 * total(before.parties))
          : seats < 0.85 * known),
    );
  }
  const terms = raw.map((term, index) => {
    const next = raw[index + 1]?.start ?? null;
    const stranded =
      next != null &&
      typical != null &&
      Date.parse(next) - Date.parse(term.start) > 3 * typical
        ? new Date(Date.parse(term.start) + typical).toISOString().slice(0, 10)
        : null;
    const lapse = body.sizes.find(
      (entry) =>
        entry.from != null &&
        entry.from <= term.start &&
        entry.to != null &&
        entry.to > term.start &&
        body.sizes.every((other) => other.from !== entry.to) &&
        next != null &&
        Date.parse(next) - Date.parse(entry.to) > 3.2e10,
    )?.to;
    const seats = new Map<string | null, number>();
    for (const entry of term.parties) {
      const key = effective({
        ...entry,
        party:
          entry.party == null ? null : (kin.get(entry.party) ?? entry.party),
        caucus:
          "caucus" in entry && entry.caucus != null
            ? (kin.get(entry.caucus) ?? entry.caucus)
            : null,
      });
      seats.set(key, (seats.get(key) ?? 0) + entry.seats);
    }
    const end = lapse ?? stranded ?? next ?? body.closed;
    const settled = new Date(Date.parse(term.start) + 30 * 864e5)
      .toISOString()
      .slice(0, 10);
    const steps = term.isSeated
      ? days
          .filter(
            (day) =>
              day.date > settled &&
              (end == null || day.date < end) &&
              day.moves.length > 0,
          )
          .map(({ date, parties, near, moves }) => ({
            date,
            parties,
            near,
            moves,
            undated: term.undated.filter((to) => to == null || to > date)
              .length,
          }))
      : [];
    return {
      ...term,
      parties: [...seats].map(([party, count]) => ({ party, seats: count })),
      steps,
      undated: term.undated.filter((to) => to == null || to > settled).length,
      near: days.findLast((day) => day.date <= settled)?.near ?? {},
      end,
      total: total(term.parties),
      size: size(term.start),
      isPartial: partial[index] ?? false,
    };
  });
  const recent = new Map<string, number>();
  for (const term of terms) {
    for (const [index, parties] of [
      term.parties,
      ...term.steps.map((step) => step.parties),
    ].entries()) {
      for (const entry of parties) {
        for (const part of entry.party?.split(">") ?? []) {
          if (index === 0 || !recent.has(part)) {
            recent.set(part, entry.seats);
          }
        }
      }
    }
  }
  const relit = new Map(
    groups.flatMap((group) =>
      group.flatMap((party) =>
        party.color == null ? [] : [[party.id, party.color] as const],
      ),
    ),
  );
  separate(relit, [...recent]);
  const spans = await db.execute<{
    start: string;
    id: string;
    kind: string;
    lang: string;
    text: string;
    since: string | null;
    until: string | null;
  }>(sql`
    select t.start::text as start, n.entity_id::text as id, n.kind::text as kind, n.lang, n.text,
      lower(n.valid)::text as since, upper(n.valid)::text as until
    from unnest(${sql.param(terms.map((term) => term.start))}::date[], ${sql.param(terms.map((term) => term.end ?? null))}::date[]) t(start, finish)
    join name n on n.valid && daterange(t.start, t.finish) and not (lower_inf(n.valid) and upper_inf(n.valid))
      and n.kind in ('official', 'english', 'native', 'abbreviation')
      and n.entity_id = any(${sql.param([id, ...recent.keys().filter((key) => /^[\da-f-]{36}$/u.test(key))])}::uuid[])
    order by n.valid @> t.start desc, lower(n.valid), n.is_preferred desc, (n.lang = 'en') desc, n.recorded_from desc
  `);
  const at = Map.groupBy(spans.rows, (row) => row.start);
  const timeline = await eras(
    recent
      .keys()
      .filter((key) => /^[\da-f-]{36}$/u.test(key))
      .toArray(),
    terms[0]?.start ?? new Date().toISOString().slice(0, 10),
    null,
  );
  const titled = terms.map((term) => {
    const rows = at.get(term.start) ?? [];
    const own = rows.filter((row) => row.id === id);
    const native = own.find((row) => row.kind === "native")?.text;
    const english = own.find(
      (row) =>
        (row.kind === "official" || row.kind === "english") &&
        row.lang === "en",
    )?.text;
    return {
      ...term,
      title:
        native != null && english != null && native !== english
          ? `${native} (${english})`
          : (english ?? native ?? null),
      names: Object.fromEntries(
        new Set(rows.flatMap((row) => (row.id === id ? [] : [row.id])))
          .values()
          .flatMap((party) => {
            const found = era(timeline, party, term.start);
            return found == null
              ? []
              : [[party, { label: found.label, short: found.short }] as const];
          }),
      ),
    };
  });
  const last = titled.at(-1);
  const [members, marks] = await Promise.all([
    last == null ? null : call(router.bodies.members, { id, from: last.start }),
    Promise.all(
      terms.map(
        async (term) =>
          [
            term.start,
            await logos(partiesIn(term.parties), term.start),
          ] as const,
      ),
    ),
  ]);
  return {
    kind: "chamber" as const,
    id,
    body,
    council,
    terms: titled,
    eras: timeline,
    logos: Object.fromEntries(marks),
    basis: isSeated
      ? earlier.length > 0 || later.length > 0
        ? ("mixed" as const)
        : ("seated" as const)
      : ("results" as const),
    seats:
      last == null
        ? null
        : last.isPartial
          ? (last.size ?? body.sizes.at(-1)?.seats ?? null)
          : (last.size ?? last.total),
    governments: record.governments.flatMap((cabinet) =>
      body.closed == null
        ? [cabinet]
        : cabinet.from >= body.closed
          ? []
          : [
              {
                ...cabinet,
                to:
                  cabinet.to == null || cabinet.to > body.closed
                    ? body.closed
                    : cabinet.to,
              },
            ],
    ),
    parties: stand(
      terms.flatMap((term) =>
        [
          ...term.parties,
          ...term.steps.flatMap((step) => [
            ...step.parties,
            ...step.moves.flatMap((move) => [
              { party: move.from, seats: 0 },
              { party: move.to, seats: 0 },
            ]),
          ]),
        ].map((entry) => entry.party),
      ),
      groups.flatMap((group) =>
        group.map((party) => ({
          ...party,
          color: relit.get(kin.get(party.id) ?? party.id) ?? party.color,
          short:
            new Set(group.map((entry) => entry.short)).size > 1
              ? null
              : party.short,
        })),
      ),
    ),
    members,
    kin: Object.fromEntries([...kin].filter(([id, root]) => id !== root)),
    renames: body.changes
      .filter((row) => row.kind === "rename")
      .map((row) => `${row.from}|${row.to}`),
  };
}

export async function archive(path: string, search: URLSearchParams) {
  const segments = path.split("/");
  const result = await db.execute<{
    path: string;
    entity_id: string;
    type: string;
    is_current: boolean;
    current: string | null;
  }>(sql`
    select s.path, s.entity_id, e.type::text as type, s.is_current,
      (select c.path from slug c where c.entity_id = s.entity_id and c.is_current) as current
    from slug s join entity e on e.id = s.entity_id
    where s.path = any(${sql.param(segments.map((_, index) => segments.slice(0, index + 1).join("/")))}::text[])
    order by length(s.path) desc
    limit 1
  `);
  const slug = result.rows[0];
  const isDay =
    slug?.type === "polity" &&
    /^\d{4}-\d{2}-\d{2}$/u.test(path.slice(slug.path.length + 1));
  if (
    slug == null ||
    (slug.path !== path && slug.type !== "contest" && !isDay)
  ) {
    error(404);
  }
  const unit = path.slice(slug.path.length + 1);
  if (!slug.is_current && slug.current != null) {
    const query = search.toString();
    redirect(
      308,
      `/${[slug.current, unit].filter((part) => part !== "").join("/")}${query === "" ? "" : `?${query}`}`,
    );
  }
  const requested = search.get("match");
  const match =
    requested === "person" || requested === "family" ? requested : "party";
  const region = await db.execute<{ id: string }>(sql`
    with recursive up(id) as (
      select coalesce(
        (select e.polity_id from contest c join election e on e.id = c.election_id where c.id = ${slug.entity_id}::uuid),
        (select polity_id from election where id = ${slug.entity_id}::uuid),
        (select id from polity where id = ${slug.entity_id}::uuid)
      )
      union
      select pp.parent_id from polity_parent pp join up on pp.polity_id = up.id where upper_inf(pp.valid)
    )
    select up.id from up
    join polity_parent pp on pp.polity_id = up.id and upper_inf(pp.valid)
    join polity n on n.id = pp.parent_id and n.kind = 'sovereign_state'
    limit 1
  `);
  const within = region.rows[0]?.id ?? null;
  if (isDay) {
    const local = await call(router.elections.local, {
      polity: slug.entity_id,
      date: unit,
    });
    if (local == null) {
      error(404);
    }
    if (local.own != null) {
      redirect(307, `/${local.own}`);
    }
    return {
      kind: "day" as const,
      id: slug.entity_id,
      date: unit,
      local,
      within,
    };
  }
  if (slug.type === "contest") {
    const [
      contest,
      lists,
      rounds,
      ballot,
      cycle,
      handover,
      additional,
      successors,
      statutes,
      standing,
    ] = await found(
      Promise.all([
        call(router.contests.get, {
          id: slug.entity_id,
          unit,
          by: search.get("by") ?? undefined,
          vs: search.get("vs") ?? undefined,
          match,
          round: Math.max(0, Math.trunc(Number(search.get("round"))) || 0),
          group: search.get("group") ?? undefined,
        }),
        call(router.contests.lists, { id: slug.entity_id }),
        call(router.contests.rounds, { id: slug.entity_id, unit }),
        call(router.contests.ballot, { id: slug.entity_id, unit }),
        call(router.contests.cycle, { id: slug.entity_id, unit }),
        call(router.seats.change, { id: slug.entity_id }),
        call(router.seats.additional, { id: slug.entity_id }),
        call(router.seats.successors, { id: slug.entity_id }),
        call(router.seats.statutes, { id: slug.entity_id }),
        (async () => {
          if (unit === "") {
            return null;
          }
          try {
            return await call(router.contests.lean, {
              id: slug.entity_id,
              unit,
              group:
                search.get("group") === "alliance" ? "alliance" : undefined,
            });
          } catch {
            return null;
          }
        })(),
      ]),
    );
    const boundaries = await call(router.contests.boundaries, {
      id: slug.entity_id,
      unit,
      level: contest.places?.level,
    });
    const siblings = contest.scope.trail.at(-1)?.siblings;
    const level = boundaries.find(
      (entry) => entry.scheme === contest.places?.level,
    );
    const own = boundaries.find(
      (entry) => entry.k != null && entry.scheme !== contest.places?.level,
    );
    const surround = await (async () => {
      if (
        siblings == null ||
        contest.locator != null ||
        (own != null && level?.tiles != null && own.frame != null)
      ) {
        return null;
      }
      try {
        const [places, bounds] = await Promise.all([
          call(router.contests.places, {
            id: siblings.contest,
            unit: siblings.unit ?? undefined,
            by: siblings.level,
          }),
          call(router.contests.boundaries, {
            id: siblings.contest,
            unit: siblings.unit ?? undefined,
            level: siblings.level,
          }),
        ]);
        return { places, bounds };
      } catch {
        return null;
      }
    })();
    return {
      kind: "contest" as const,
      id: slug.entity_id,
      unit,
      contest,
      lists,
      rounds,
      ballot,
      cycle,
      handover,
      additional,
      successors,
      statutes,
      boundaries,
      surround,
      match,
      within,
      standing,
    };
  }
  if (slug.type === "election") {
    const input = { id: slug.entity_id };
    const place = search.get("in");
    const [scoped, held] = await Promise.all([
      db.execute<{ id: string; path: string }>(sql`
        select s.entity_id as id, s.path from slug s join polity p on p.id = s.entity_id
        where s.path = ${place} and s.is_current
      `),
      db.execute<{
        polity: string;
        date: string | null;
        tier: string | null;
      }>(sql`
        select e.polity_id as polity, e.date_from::text as date,
          case when p.kind = 'county' then 'county' when p.kind = 'federal_unit' and p.iso_code like 'US-%' then 'state' end as tier
        from election e join polity p on p.id = e.polity_id where e.id = ${slug.entity_id}::uuid
      `),
    ]);
    const scope = scoped.rows[0] ?? null;
    const day = held.rows[0];
    if (place != null && scope == null) {
      error(404);
    }
    const [
      election,
      governments,
      regions,
      tiers,
      cycle,
      alongside,
      local,
      counties,
    ] = await found(
      Promise.all([
        call(router.elections.get, { ...input, within: scope?.id }),
        call(router.elections.governments, input),
        call(router.elections.regions, input),
        call(router.elections.tiers, input),
        call(router.elections.cycle, input),
        call(router.elections.alongside, input),
        scope != null || day?.date == null || day.tier !== "county"
          ? null
          : call(router.elections.local, {
              polity: day.polity,
              date: day.date,
            }),
        scope != null || day?.date == null || day.tier !== "state"
          ? null
          : call(router.elections.counties, {
              polity: day.polity,
              date: day.date,
            }),
      ]),
    );
    return {
      kind: "election" as const,
      id: slug.entity_id,
      scope,
      election,
      cycle,
      sections: { governments, regions, tiers, alongside, local, counties },
      within,
    };
  }
  if (slug.type === "polity") {
    return {
      kind: "polity" as const,
      id: slug.entity_id,
      polity: await call(router.polities.get, { id: slug.entity_id }),
      within,
    };
  }
  error(404);
}

export async function results(search: URLSearchParams) {
  const q = (search.get("q") ?? "").trim().slice(0, 200);
  return {
    kind: "search" as const,
    q,
    hits: await call(router.search.query, { q, limit: 50 }),
  };
}

export async function comparison(ids: Array<string>) {
  const found = await db.execute<{
    id: string;
    type: string;
    name: string;
    context: string | null;
  }>(
    sql`
      select e.id, e.type::text as type, coalesce((
        select n.text from name n where n.entity_id = e.id and n.kind in ('official', 'english')
        order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc, n.recorded_from desc, n.id limit 1
      ), '') as name, (
        select n.text from name n
        where n.entity_id = case e.type
            when 'party' then (select p.polity_id from party p where p.id = e.id)
            when 'polity' then (select pp.parent_id from polity_parent pp join polity y on y.id = pp.parent_id
              where pp.polity_id = e.id order by y.kind = 'supranational' limit 1)
          end
          and n.kind in ('official', 'english')
        order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'english') desc, n.recorded_from desc, n.id limit 1
      ) as context
      from unnest(${sql.param(ids.filter((id) => /^[0-9a-f-]{36}$/u.test(id)))}::uuid[]) with ordinality as x(asked, at)
      join entity e on e.id = coalesce((select into_id from entity_merge where entity_id = x.asked), x.asked)
      where e.type in ('person', 'polity', 'party')
      order by x.at
    `,
  );
  const [first, second] = found.rows;
  const examples =
    ids.length > 0
      ? null
      : await db.execute<{
          a: string;
          b: string;
          kind: "person" | "polity" | "party";
          year: number | null;
          sides: Array<{
            id: string;
            name: string;
            photo: string | null;
            color: string | null;
            party: string | null;
          }>;
        }>(sql`
          with pairs(at, a, b, kind) as (
            values (1, 'Donald J. Trump', 'Kamala D. Harris', 'person'),
              (2, 'us/ga', 'us/nc', 'polity'),
              (3, 'Recep Tayyip Erdoğan', 'Kemal Kılıçdaroğlu', 'person'),
              (4, 'us/pa', 'us/wi', 'polity'),
              (5, 'Ekrem İmamoğlu', 'Binali Yıldırım', 'person'),
              (6, 'Cumhuriyet Halk Partisi', 'Adalet ve Kalkınma Partisi', 'party')
          ),
          picked as (
            select p.at, p.kind, x.side, coalesce(
              (select s.entity_id from slug s where p.kind = 'polity' and s.is_current and s.path = x.key limit 1),
              (select n.entity_id from name n join entity e on e.id = n.entity_id and e.type = 'person'
                where p.kind = 'person' and n.text = x.key limit 1),
              (select n.entity_id from name n join entity e on e.id = n.entity_id and e.type = 'party'
                where p.kind = 'party' and n.text = x.key limit 1)
            ) as id
            from pairs p cross join lateral (values (1, p.a), (2, p.b)) x(side, key)
          ),
          sided as (
            select k.at, k.side, k.id,
              (select n.text from name n where n.entity_id = k.id order by n.is_preferred desc, (n.lang = 'en') desc, n.recorded_from desc, n.id limit 1) as name,
              (select '/' || i.path from person_image i where i.person_id = k.id order by i.recorded_from desc limit 1) as photo,
              case when k.kind = 'party' then k.id else (
                select o.party_id from candidacy cy
                join contest_option o on o.id = cy.option_id
                join contest c on c.id = o.contest_id
                join election e on e.id = c.election_id
                where k.kind = 'person' and cy.person_id = k.id and cy.role = 'head' and o.party_id is not null
                order by e.date_from desc limit 1
              ) end as party
            from picked k
            where k.id is not null
          )
          select a.id as a, b.id as b, a.kind, case a.kind
            when 'person' then (
              select max(extract(year from e.date_from))::int
              from candidacy x
              join contest_option ox on ox.id = x.option_id
              join contest_option oy on oy.contest_id = ox.contest_id
              join candidacy y on y.option_id = oy.id and y.role = 'head' and y.person_id = b.id
              join contest c on c.id = ox.contest_id
              join election e on e.id = c.election_id
              where x.person_id = a.id and x.role = 'head'
            )
            when 'party' then (
              select min(extract(year from e.date_from))::int from election e
              where exists (select from contest c join contest_option o on o.contest_id = c.id where c.election_id = e.id and o.party_id = a.id)
                and exists (select from contest c join contest_option o on o.contest_id = c.id where c.election_id = e.id and o.party_id = b.id)
            )
            else (
              select greatest(
                min(extract(year from e.date_from)) filter (where e.polity_id = a.id),
                min(extract(year from e.date_from)) filter (where e.polity_id = b.id)
              )::int
              from election e where e.polity_id in (a.id, b.id)
            ) end as year,
            (
              select json_agg(json_build_object(
                'id', s.id, 'name', s.name, 'photo', s.photo, 'party', s.party,
                'color', (select pc.color from party_color pc where pc.party_id = s.party and upper_inf(pc.valid))
              ) order by s.side)
              from sided s where s.at = a.at
            ) as sides
          from picked a join picked b on b.at = a.at and b.side = 2
          where a.side = 1 and a.id is not null and b.id is not null
          order by a.at
        `);
  const sided =
    first?.type === "party" && second?.type === "party"
      ? await Promise.all(
          [first, second].map(async (row) => {
            const [party, history] = await Promise.all([
              call(router.parties.get, { id: row.id }),
              call(router.parties.history, { id: row.id }),
            ]);
            return { id: row.id, party, history };
          }),
        )
      : null;
  const [one, two] = sided ?? [];
  const suggested =
    second != null || first == null
      ? null
      : first.type === "party"
        ? sql`
          select o.party_id as id, sum(x.seats) as rank, 0 as tie
          from contest_option o
          cross join lateral (
            select max(coalesce(x.seats, 1)) as seats from outcome x
            join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
            where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
            having count(*) > 0
          ) x
          where o.contest_id in (select contest_id from contest_option where party_id = ${first.id}::uuid)
            and o.party_id <> ${first.id}::uuid
          group by o.party_id
          order by rank desc
          limit 6
        `
        : first.type === "person"
          ? sql`
            with mine as materialized (
              select distinct o.contest_id, o.id as option
              from candidacy cy join contest_option o on o.id = cy.option_id
              left join entity_merge m on m.entity_id = cy.person_id
              where cy.role = 'head' and coalesce(m.into_id, cy.person_id) = ${first.id}::uuid
            ),
            places as materialized (
              select c.id as contest_id, rs.k as set, a.k as area
              from (select distinct contest_id from mine) x
              join contest c on c.id = x.contest_id
              join election e on e.id = c.election_id
              join result_set rs on rs.contest_id = c.id and ${latest("rs")}
              join area a on a.id = coalesce(c.district_area_id, (
                select t.area_id from polity_territory t where t.polity_id = e.polity_id and t.valid @> e.date_from
              ))
            ),
            placed as materialized (
              select contest_id, top, rank() over (partition by contest_id order by max(value) desc) as place
              from (
                select o.contest_id, coalesce(o.parent_option_id, o.id) as top, sum(r.value) as value
                from places pl
                join contest_option o on o.contest_id = pl.contest_id
                cross join lateral (
                  select r.value, r.count_round from result_rollup r
                  where r.result_set_k = pl.set and r.area_k = pl.area and r.option_k = o.k
                    and r.measure = 1 and r.channel = 0 offset 0
                ) r
                group by o.contest_id, top, r.count_round
              ) rounds
              group by contest_id, top
            ),
            close as materialized (
              select y.contest_id from placed y join mine on mine.option = y.top where y.place <= 2
            )
            select coalesce(m.into_id, cy.person_id) as id,
              count(distinct o.contest_id) filter (where x.place <= 2 and o.contest_id in (select contest_id from close)) as rank,
              count(distinct o.contest_id) as tie
            from contest_option o
            join candidacy cy on cy.option_id = o.id and cy.role = 'head'
            left join entity_merge m on m.entity_id = cy.person_id
            left join placed x on x.contest_id = o.contest_id and x.top = o.id
            where o.contest_id in (select contest_id from mine) and coalesce(m.into_id, cy.person_id) <> ${first.id}::uuid
            group by 1
            order by rank desc, tie desc
            limit 6
          `
          : sql`
            with own as materialized (
              select pp.parent_id, y.kind, g.geom
              from polity y
              join polity_parent pp on pp.polity_id = y.id and upper_inf(pp.valid)
              join polity_territory t on t.polity_id = y.id and upper_inf(t.valid)
              join area_geometry g on g.area_id = t.area_id and upper_inf(g.valid)
              where y.id = ${first.id}::uuid
              limit 1
            )
            select s.id, -ST_Distance(g.geom, own.geom) as rank, ST_Area(g.geom) as tie
            from own
            join polity_parent pp on pp.parent_id = own.parent_id and upper_inf(pp.valid) and pp.polity_id <> ${first.id}::uuid
            join polity s on s.id = pp.polity_id and s.kind = own.kind
            join polity_territory t on t.polity_id = s.id and upper_inf(t.valid)
            join area_geometry g on g.area_id = t.area_id and upper_inf(g.valid)
            where exists (select from election e where e.polity_id = s.id)
            order by rank desc, tie desc
            limit 6
          `;
  const rivals =
    suggested == null
      ? null
      : await db.execute<{ id: string; name: string }>(sql`
        select r.id, (
          select n.text from name n where n.entity_id = r.id
          order by n.is_preferred desc, (n.lang = 'en') desc, n.recorded_from desc, n.id limit 1
        ) as name
        from (${suggested}) r
        order by r.rank desc, r.tie desc
      `);
  return {
    kind: "comparison" as const,
    sides: found.rows,
    examples: examples?.rows ?? [],
    rivals: rivals?.rows ?? [],
    people:
      first?.type === "person" && second?.type === "person"
        ? await Promise.all(
            [first, second].map(async (row) => ({
              id: row.id,
              ...(await call(router.people.get, { id: row.id })),
            })),
          )
        : null,
    places:
      first?.type === "polity" && second?.type === "polity"
        ? await call(router.polities.compare, { a: first.id, b: second.id })
        : null,
    parties:
      sided == null
        ? null
        : {
            parties: [one, two].map((side) => ({
              id: side?.id ?? "",
              name: side?.party.name ?? "",
              short: side?.party.short ?? null,
              color: side?.party.color ?? null,
            })),
            series: (one?.history ?? []).flatMap((series) => {
              const other = two?.history.find(
                (entry) => entry.id === series.id,
              );
              if (other == null) {
                return [];
              }
              const dates = [
                ...new Set(
                  [...series.points, ...other.points].map(
                    (point) => point.date,
                  ),
                ),
              ].toSorted((a, b) => a.localeCompare(b));
              const points = dates.map((date) => {
                const sides = [series, other].map((entry) => {
                  const point = entry.points.find(
                    (candidate) => candidate.date === date,
                  );
                  return point?.votes == null || point.total == null
                    ? null
                    : {
                        share: point.votes / point.total,
                        seats: point.seats,
                        filled: point.filled,
                      };
                });
                const any =
                  series.points.find((point) => point.date === date) ??
                  other.points.find((point) => point.date === date);
                return {
                  date,
                  year: any?.year ?? Number(date.slice(0, 4)),
                  name: any?.name ?? "",
                  path: any?.path ?? null,
                  sides,
                };
              });
              return points.some((point) =>
                point.sides.every((side) => side != null),
              )
                ? [{ id: series.id, name: series.name, points }]
                : [];
            }),
          },
  };
}

export async function atlasPage(path: string, search: URLSearchParams) {
  const segments = path.split("/");
  let slug:
    | {
        path: string;
        entity_id: string;
        is_current: boolean;
        current: string | null;
      }
    | undefined;
  let whole: string | null = null;
  for (const type of ["contest", "election"]) {
    const result = await db.execute<{
      path: string;
      entity_id: string;
      is_current: boolean;
      current: string | null;
    }>(sql`
      select s.path, s.entity_id, s.is_current,
        (select c.path from slug c where c.entity_id = s.entity_id and c.is_current) as current
      from slug s join entity e on e.id = s.entity_id
      where e.type = ${type}
        and s.path = any(${sql.param(segments.map((_, index) => segments.slice(0, index + 1).join("/")))}::text[])
      order by length(s.path) desc
      limit 1
    `);
    const held = result.rows[0];
    if (held == null) {
      continue;
    }
    if (type === "contest") {
      slug = held;
      break;
    }
    const anchor = path.slice(held.path.length + 1).split("/", 1)[0] ?? "";
    const id =
      anchor === "" ? undefined : await representative(held.entity_id, anchor);
    if (id != null) {
      slug = {
        path: `${held.path}/${anchor}`,
        entity_id: id,
        is_current: held.is_current,
        current: held.current == null ? null : `${held.current}/${anchor}`,
      };
      whole = held.entity_id;
    }
  }
  if (slug == null) {
    error(404);
  }
  const unit = path.slice(slug.path.length + 1);
  if (!slug.is_current && slug.current != null) {
    redirect(
      308,
      `/map/${[slug.current, unit].filter((part) => part !== "").join("/")}${search.size > 0 ? `?${search}` : ""}`,
    );
  }
  return {
    kind: "map" as const,
    contest: slug.entity_id,
    whole,
    path: slug.is_current ? slug.path : (slug.current ?? slug.path),
    atlas: await found(
      call(router.contests.atlas, {
        id: slug.entity_id,
        unit: unit === "" ? undefined : unit,
        level: search.get("level") ?? undefined,
        vs: search.get("vs") ?? undefined,
        group: search.get("group") ?? undefined,
        whole: whole ?? undefined,
      }),
    ),
  };
}

export async function livePage(path: string, at: string | null) {
  const night = await found(call(router.live.get, { path }));
  const hasFinal = night.races.some((race) => race.certified != null);
  const wanted = Date.parse(at ?? "");
  const position =
    at === "final" && hasFinal
      ? night.frames.length
      : at == null || Number.isNaN(wanted)
        ? night.frames.length - 1
        : Math.max(
            0,
            night.frames.findLastIndex(
              (frame) => Date.parse(frame.at) <= wanted,
            ),
          );
  const isFinal = hasFinal && position === night.frames.length;
  const time = isFinal ? null : (night.frames[position]?.at ?? null);
  return {
    kind: "live" as const,
    path,
    ...night,
    at: isFinal ? "final" : time,
    scanners: Object.entries(
      Object.fromEntries(
        night.frames
          .slice(0, position + 1)
          .flatMap((frame) =>
            frame.scanners.map((entry) => [entry.area, entry.share]),
          ),
      ) as Record<string, number>,
    ).map(([area, share]) => ({ area, share })),
    races: night.races.map((race) => {
      const current =
        race.series.findLast((point) => time == null || point.at <= time) ??
        null;
      return {
        ...race,
        standing:
          isFinal && race.certified != null
            ? { at: "final", level: null, votes: race.certified.votes }
            : current,
      };
    }),
  };
}
