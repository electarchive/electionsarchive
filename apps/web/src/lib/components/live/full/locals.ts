import type { LiveEstimate, LiveRace } from "@ea/analytics/live";
import type { BallotLocals } from "#lib/server/live-ballot.js";
import { stub } from "#lib/live/ballot.js";
import { orpc } from "#lib/rpc.js";

const ballots = new Map<string, Promise<BallotLocals | null>>();
const results = new Map<
  string,
  {
    stamp: string;
    value: Promise<{ race: LiveRace; estimate: LiveEstimate | null } | null>;
  }
>();

export async function ballot(slug: string) {
  let found = ballots.get(slug);
  if (found == null) {
    found = (async () => {
      const response = await fetch(`/live/ballot/${slug}`);
      return response.ok ? ((await response.json()) as BallotLocals) : null;
    })().catch(() => null);
    ballots.set(slug, found);
  }
  return found;
}

export function contests(found: BallotLocals, county: string) {
  return found.places
    .filter(
      (place) => place.name === county || place.name.startsWith(`${county} `),
    )
    .flatMap((place) =>
      place.contests.map((contest) => ({ place: place.name, contest })),
    );
}

async function result(slug: string, stamp: string) {
  const held = results.get(slug);
  if (held?.stamp === stamp) {
    return held.value;
  }
  const value = (async () => {
    const found = await orpc.live.election.call({ path: slug });
    const race = found.election.races[0];
    return race == null
      ? null
      : { race, estimate: found.estimates[race.id] ?? null };
  })().catch(() => null);
  results.set(slug, { stamp, value });
  return value;
}

export async function live(
  found: BallotLocals,
  picked: ReturnType<typeof contests>,
  state: string,
  stamp: string,
) {
  const out: Array<{
    index: number;
    place: string;
    race: LiveRace;
    estimate: LiveEstimate | null;
  }> = [];
  const queue = picked.map((entry, index) => ({ ...entry, index }));
  const worker = async () => {
    for (let next = queue.shift(); next != null; next = queue.shift()) {
      const loaded = await result(next.contest.slug, stamp);
      out.push({
        index: next.index,
        place: next.place,
        race:
          loaded?.race ??
          stub(next.contest, state, found.parties, "other", null, "live"),
        estimate: loaded?.estimate ?? null,
      });
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  return out.toSorted((a, b) => a.index - b.index);
}
