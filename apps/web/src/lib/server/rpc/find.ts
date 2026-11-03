import { os } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Hit } from "#lib/live/find.js";
import type { BallotTier } from "#lib/server/live-ballot.js";
import { haystack, score, strongest, terms } from "#lib/live/find.js";
import { remember } from "#lib/server/cache.js";
import { liveDb as db } from "#lib/server/db/index.js";

type Entry = Omit<Hit, "match"> & {
  rank: number;
  code: string;
  names: Array<string>;
};

const TIERS: Partial<Record<BallotTier, number>> = {
  statewide: 0,
  measures: 1,
  courts: 2,
  upper: 2,
  lower: 2,
};

async function catalog(path: string) {
  return remember(`live:find:v1:${path}`, async () => {
    const { ballotLocals, liveBallot } =
      await import("#lib/server/live-ballot.js");
    const found = await db.execute<{ path: string }>(sql`
      select c.path from slug s join election e on e.id = s.entity_id
      join election x on x.parent_id = e.id
      join slug c on c.entity_id = x.id and c.is_current
      where s.path = ${path} and s.is_current
      order by c.path
    `);
    const queue = found.rows
      .map((row) => row.path)
      .filter((entry) => /^us\/[a-z]{2}\/[^/]+$/u.test(entry));
    const entries: Array<Entry> = [];
    async function work() {
      let next = queue.shift();
      while (next != null) {
        const code = next.split("/", 2)[1] ?? "";
        const [ballot, locals] = await Promise.all([
          liveBallot(next),
          ballotLocals(next),
        ]);
        const state = ballot?.name ?? code.toUpperCase();
        for (const { tier, contest } of ballot?.contests ?? []) {
          const rank = TIERS[tier];
          if (rank != null) {
            entries.push({
              slug: contest.slug,
              title: contest.title,
              place: state,
              state,
              code,
              isMeasure: contest.isMeasure,
              isLocal: false,
              rank,
              names: contest.isMeasure
                ? []
                : contest.options.flatMap((option) =>
                    option.isWriteIn ? [] : [option.name],
                  ),
            });
          }
        }
        for (const place of locals?.places ?? []) {
          for (const contest of place.contests) {
            entries.push({
              slug: contest.slug,
              title: contest.title,
              place: place.name,
              state,
              code,
              isMeasure: contest.isMeasure,
              isLocal: true,
              rank: contest.isMeasure ? 3 : 4,
              names: contest.isMeasure
                ? []
                : contest.options.flatMap((option) =>
                    option.isWriteIn ? [] : [option.name],
                  ),
            });
          }
        }
        next = queue.shift();
      }
    }
    await Promise.all(Array.from({ length: 6 }, async () => work()));
    return entries;
  });
}

const prepared = new WeakMap<
  Array<Entry>,
  Array<{
    entry: Entry;
    hay: string;
    people: Array<{ name: string; hay: string }>;
  }>
>();

function index(entries: Array<Entry>) {
  let found = prepared.get(entries);
  if (found == null) {
    found = entries.map((entry) => ({
      entry,
      hay: haystack([
        entry.title,
        entry.place,
        entry.state,
        entry.code,
        ...(entry.isMeasure ? ["measure"] : []),
      ]),
      people: entry.names.map((name) => ({ name, hay: haystack([name]) })),
    }));
    prepared.set(entries, found);
  }
  return found;
}

export const find = os
  .input(
    v.object({
      path: v.pipe(v.string(), v.regex(/^us\/\d{4}-\d{2}-\d{2}-[a-z-]+$/u)),
      q: v.pipe(v.string(), v.trim(), v.maxLength(80)),
    }),
  )
  .handler(async ({ input }): Promise<Array<Hit>> => {
    const wanted = terms(input.q);
    const entries = index(await catalog(input.path));
    if (wanted.length === 0) {
      return [];
    }
    const hits: Array<{ entry: Entry; total: number; match: string | null }> =
      [];
    for (const { entry, hay, people } of entries) {
      const found = score(hay, people, wanted);
      if (found != null) {
        hits.push({ entry, ...found });
      }
    }
    return strongest(
      hits.toSorted(
        (a, b) =>
          b.total - a.total ||
          a.entry.rank - b.entry.rank ||
          a.entry.title.length - b.entry.title.length,
      ),
    )
      .slice(0, 10)
      .map(({ entry, match }) => ({
        slug: entry.slug,
        title: entry.title,
        place: entry.place,
        state: entry.state,
        isMeasure: entry.isMeasure,
        isLocal: entry.isLocal,
        match,
      }));
  });
