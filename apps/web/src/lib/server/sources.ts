import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Held as Counted, Supply } from "#lib/source-kinds.js";
import { remember } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { CATEGORIES, SUPPLIES } from "#lib/source-kinds.js";
import catalog from "#lib/sources.json";

const Link = v.object({ name: v.string(), url: v.string() });

const Catalog = v.object({
  entries: v.array(
    v.object({
      id: v.string(),
      category: v.picklist(CATEGORIES),
      name: v.string(),
      url: v.optional(v.nullable(v.string()), null),
      countries: v.array(v.string()),
      supplies: v.array(v.picklist(SUPPLIES)),
      license: v.optional(v.string()),
      about: v.optional(v.string()),
      quote: v.optional(
        v.object({ text: v.string(), url: v.string(), lang: v.string() }),
      ),
      publishers: v.optional(v.array(v.string()), []),
      isGroup: v.optional(v.boolean(), false),
      match: v.optional(v.string()),
      kinds: v.optional(v.array(v.string()), []),
      rank: v.optional(v.number()),
      feeds: v.optional(v.array(Link), []),
    }),
  ),
});

const { entries } = v.parse(Catalog, catalog);

type Held = Counted & { countries: Array<string> };

type Row = Held & {
  publisher: string;
  url: string | null;
  kinds: Array<string>;
};

export type Member = Held & { name: string; url: string | null };

export type Source = Held & {
  id: string;
  name: string;
  url: string | null;
  supplies: Array<Supply>;
  license: string | null;
  about: string | null;
  quote: { text: string; url: string; lang: string } | null;
  isGroup: boolean;
  isUnofficial: boolean;
  publishers: number;
  members: Array<Member>;
  feeds: Array<{ name: string; url: string }>;
};

const exact = new Map<string, string>();
for (const entry of entries) {
  for (const publisher of entry.publishers) {
    exact.set(publisher, entry.id);
  }
}

const groups = entries
  .filter((entry) => entry.rank != null)
  .toSorted((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
  .map((entry) => ({
    id: entry.id,
    kinds: new Set(entry.kinds),
    pattern: entry.match == null ? null : new RegExp(entry.match, "u"),
  }));

function owner(row: Row) {
  return (
    exact.get(row.publisher) ??
    groups.find(
      (group) =>
        row.kinds.some((kind) => group.kinds.has(kind)) ||
        group.pattern?.test(row.publisher) === true,
    )?.id ??
    null
  );
}

function tally(rows: Array<Held>, countries: Array<string>): Held {
  const found = new Set(countries);
  let sets = 0;
  let contests = 0;
  let shapes = 0;
  let first: number | null = null;
  let last: number | null = null;
  for (const row of rows) {
    sets += row.sets;
    contests += row.contests;
    shapes += row.shapes;
    if (row.first != null && (first == null || row.first < first)) {
      first = row.first;
    }
    if (row.last != null && (last == null || row.last > last)) {
      last = row.last;
    }
    for (const country of row.countries) {
      found.add(country);
    }
  }
  return {
    sets,
    contests,
    shapes,
    first,
    last,
    countries: [...found].toSorted((a, b) => a.localeCompare(b)),
  };
}

async function held() {
  return (
    await db.execute<Row>(sql`
      with by_polity as (
        select r.source_id, e.polity_id, count(*) as sets,
          count(distinct r.contest_id) as contests,
          min(e.date_from) as first, max(e.date_from) as last
        from result_set r join election e on e.id = r.election_id
        where r.status = 'active'
        group by 1, 2
      ), polities as (
        select distinct polity_id from by_polity
      ), up as (
        with recursive walk as (
          select polity_id, polity_id as at, 0 as depth from polities
          union all
          select w.polity_id, pp.parent_id, w.depth + 1
          from walk w join polity_parent pp on pp.polity_id = w.at
          where w.depth < 8 and not exists (
            select 1 from polity p where p.id = w.at and p.iso_code is not null
          )
        )
        select distinct on (w.polity_id) w.polity_id,
          split_part(p.iso_code, '-', 1) as country
        from walk w join polity p on p.id = w.at
        where p.iso_code is not null
        order by w.polity_id, w.depth
      ), counted as (
        select s.publisher, sum(b.sets)::int as sets,
          sum(b.contests)::int as contests,
          extract(year from min(b.first))::int as first,
          extract(year from max(b.last))::int as last,
          array_agg(distinct up.country) filter (where up.country is not null) as countries
        from by_polity b
        join source s on s.id = b.source_id
        left join up on up.polity_id = b.polity_id
        group by 1
      ), drawn as (
        select s.publisher, count(*)::int as shapes
        from area_geometry g join source s on s.id = g.source_id
        group by 1
      )
      select s.publisher, min(s.url) as url,
        array_agg(distinct s.kind::text) as kinds,
        coalesce(c.sets, 0) as sets, coalesce(c.contests, 0) as contests,
        coalesce(d.shapes, 0) as shapes, c.first, c.last,
        coalesce(c.countries, '{}') as countries
      from source s
      left join counted c on c.publisher = s.publisher
      left join drawn d on d.publisher = s.publisher
      where s.publisher is not null
      group by s.publisher, c.sets, c.contests, d.shapes, c.first, c.last, c.countries
    `)
  ).rows;
}

export async function listSources() {
  return remember("sources:v5", async () => {
    const rows = await held();
    const owned = new Map<string, Array<Row>>();
    for (const row of rows) {
      const id = owner(row);
      if (id != null) {
        owned.set(id, [...(owned.get(id) ?? []), row]);
      }
    }
    const categories = CATEGORIES.map((category) => ({
      id: category,
      sources: entries
        .filter((entry) => entry.category === category)
        .toSorted((a, b) => Number(a.isGroup) - Number(b.isGroup))
        .map((entry): Source => {
          const mine = owned.get(entry.id) ?? [];
          const fallback = entry.countries.length === 1 ? entry.countries : [];
          return {
            ...tally(mine, entry.countries),
            id: entry.id,
            name: entry.name,
            url: entry.url,
            supplies: entry.supplies,
            license: entry.license ?? null,
            about: entry.about ?? null,
            quote: entry.quote ?? null,
            isGroup: entry.isGroup,
            isUnofficial: entry.id === "civicapi",
            publishers: entry.isGroup
              ? mine.length + entry.feeds.length
              : Math.max(mine.length, 1),
            members: entry.isGroup
              ? mine
                  .map((row) => ({
                    name: row.publisher,
                    url: row.url,
                    sets: row.sets,
                    contests: row.contests,
                    shapes: row.shapes,
                    first: row.first,
                    last: row.last,
                    countries:
                      row.countries.length > 0 ? row.countries : fallback,
                  }))
                  .toSorted(
                    (a, b) =>
                      b.contests - a.contests ||
                      b.shapes - a.shapes ||
                      a.name.localeCompare(b.name),
                  )
              : [],
            feeds: entry.feeds,
          };
        }),
    }));
    return { total: rows.length, categories };
  });
}

export type Sources = Awaited<ReturnType<typeof listSources>>;
