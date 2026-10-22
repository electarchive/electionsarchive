import { call, ORPCError } from "@orpc/server";
import { error, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
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
          where e.parent_id is null and e.date_from > current_date
          order by e.date_from limit 4)
        union all
        (select e.id, e.date_from, e.kind, e.polity_id, 'today'
          from election e
          where e.parent_id is null and e.date_from < current_date - 300
            and abs(extract(doy from e.date_from) - extract(doy from current_date)) <= 3
          order by (e.kind = 'general') desc, e.date_from desc limit 8)
      )
      select p.id, p.date_from::text as date, p.kind::text as kind, p.when, s.path,
        (select n.text from name n where n.entity_id = p.id and n.kind in ('official', 'english')
          order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc limit 1) as name,
        (select n.text from name n where n.entity_id = p.polity_id and n.kind in ('official', 'english')
          order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc limit 1) as polity
      from picked p join slug s on s.entity_id = p.id and s.is_current
    `),
    db.execute<{ id: string; years: Array<number> }>(sql`
      select e.polity_id as id,
        array_agg(distinct extract(year from e.date_from)::int) as years
      from election e join polity y on y.id = e.polity_id
      where e.parent_id is null and y.kind in ('sovereign_state', 'supranational')
        and e.kind = 'general' and e.date_from <= current_date
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
  return {
    kind: "home" as const,
    polities: covered,
    atlas: shapes,
    recent: elections.filter((election) => election.when === "recent"),
    upcoming: elections
      .filter((election) => election.when === "upcoming")
      .toSorted((a, b) => a.date.localeCompare(b.date)),
    today: elections.filter((election) => election.when === "today"),
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
  const [party, record, history, latest] = await Promise.all([
    call(router.parties.get, { id, within }),
    call(router.parties.elections, { id, within }),
    call(router.parties.history, { id, within }),
    db.execute<{ id: string }>(sql`
      with ids as (
        select ${id}::uuid as id
        union
        select entity_id from entity_merge where into_id = ${id}::uuid
        union
        select party_id from party_affiliation where kind = 'regional_branch' and parent_party_id = ${id}::uuid
      )
      select e.id from election e
      where e.kind = 'general' and e.date_from <= current_date
        and case when ${within ?? null}::uuid is null
          then e.parent_id is null and e.polity_id = (select polity_id from party where id = ${id}::uuid)
          else e.polity_id = ${within ?? null}::uuid end
        and exists (
          select from contest c join contest_option o on o.contest_id = c.id
          where c.election_id = e.id and o.party_id in (select id from ids)
        )
      order by e.date_from desc limit 1
    `),
  ]);
  return {
    kind: "party" as const,
    id,
    party,
    record,
    history,
    election: latest.rows[0]?.id ?? null,
  };
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
  const [body, record] = await Promise.all([
    call(router.bodies.get, { id }),
    call(router.polities.record, { id: polity, body: id }),
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
  const terms = raw.map((term, index) => {
    const next = raw[index + 1]?.start ?? null;
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
    return {
      ...term,
      end: lapse ?? next,
      total: total(term.parties),
      size: size(term.start),
    };
  });
  const last = terms.at(-1);
  const members =
    last == null
      ? null
      : await call(router.bodies.members, { id, from: last.start });
  return {
    kind: "chamber" as const,
    id,
    body,
    terms,
    basis: isSeated
      ? earlier.length > 0 || later.length > 0
        ? ("mixed" as const)
        : ("seated" as const)
      : ("results" as const),
    governments: record.governments,
    parties: [
      ...record.parties,
      ...body.parties.filter((party) =>
        record.parties.every((entry) => entry.id !== party.id),
      ),
    ],
    members,
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
  if (slug == null || (slug.path !== path && slug.type !== "contest")) {
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
  if (slug.type === "contest") {
    const [contest, lists, rounds, ballot, cycle] = await found(
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
      ]),
    );
    const boundaries = await call(router.contests.boundaries, {
      id: slug.entity_id,
      unit,
      level: contest.places?.level,
    });
    return {
      kind: "contest" as const,
      contest,
      lists,
      rounds,
      ballot,
      cycle,
      boundaries,
      match,
      within,
    };
  }
  if (slug.type === "election") {
    const input = { id: slug.entity_id };
    const [election, governments, regions, tiers, cycle] = await Promise.all([
      call(router.elections.get, input),
      call(router.elections.governments, input),
      call(router.elections.regions, input),
      call(router.elections.tiers, input),
      call(router.elections.cycle, input),
    ]);
    return {
      kind: "election" as const,
      id: slug.entity_id,
      election,
      cycle,
      sections: { governments, regions, tiers },
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

export async function comparison(ids: Array<string>) {
  const found = await db.execute<{ id: string; type: string; name: string }>(
    sql`
      select e.id, e.type::text as type, coalesce((
        select n.text from name n where n.entity_id = e.id and n.kind in ('official', 'english')
        order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc limit 1
      ), '') as name
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
          first: string;
          second: string;
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
            select p.at, x.side, coalesce(
              (select s.entity_id from slug s where p.kind = 'polity' and s.is_current and s.path = x.key limit 1),
              (select n.entity_id from name n join entity e on e.id = n.entity_id and e.type = 'person'
                where p.kind = 'person' and n.text = x.key limit 1),
              (select n.entity_id from name n join entity e on e.id = n.entity_id and e.type = 'party'
                where p.kind = 'party' and n.text = x.key limit 1)
            ) as id
            from pairs p cross join lateral (values (1, p.a), (2, p.b)) x(side, key)
          )
          select a.id as a, b.id as b,
            (select n.text from name n where n.entity_id = a.id order by n.is_preferred desc, (n.lang = 'en') desc limit 1) as first,
            (select n.text from name n where n.entity_id = b.id order by n.is_preferred desc, (n.lang = 'en') desc limit 1) as second
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
          select o.party_id as id, sum(coalesce(x.seats, 1)) as rank, 0 as tie
          from contest_option o
          join outcome x on x.option_id = o.id and x.kind in ('elected', 'won')
          join result_set rs on rs.id = x.result_set_id and rs.is_default
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
              join result_set rs on rs.contest_id = c.id and rs.is_default
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
          order by n.is_preferred desc, (n.lang = 'en') desc limit 1
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
