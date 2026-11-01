import type { LiveOption, LiveRace } from "@ea/analytics/live";
import { getContext, setContext } from "svelte";
import { surname } from "#lib/live/format.js";
import { sum } from "#lib/live/standing.js";
import { m } from "#lib/paraglide/messages.js";
import { getLocale } from "#lib/paraglide/runtime.js";

export type Line = { option: LiveOption; votes: number; share: number | null };

const KEY = Symbol("live:reach");

export function reach(races: Iterable<LiveRace>) {
  const counts = new Map<string, number>();
  for (const race of races) {
    for (const id of new Set(
      race.options.flatMap((option) => option.party?.id ?? []),
    )) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
  }
  return counts;
}

export function provideReach(read: () => ReadonlyMap<string, number>) {
  setContext(KEY, read);
}

export function useReach() {
  const read = getContext<(() => ReadonlyMap<string, number>) | undefined>(KEY);
  return () => read?.() ?? new Map<string, number>();
}

export function lineup(
  race: LiveRace,
  totals: Array<number>,
  parties: ReadonlyMap<string, number>,
  limit = Infinity,
) {
  const total = sum(totals);
  const prior = race.options.map((_, index) =>
    sum(race.baseline.map((row) => (row.shares[index] ?? 0) * row.turnout)),
  );
  const priorTotal = sum(prior);
  const ranked = race.options
    .map((option, index) => {
      const votes = totals[index] ?? 0;
      return {
        option,
        index,
        votes,
        share: total > 0 ? votes / total : null,
        weight:
          total > 0
            ? votes / total
            : priorTotal > 0
              ? (prior[index] ?? 0) / priorTotal
              : 0,
        reach: option.party == null ? 0 : (parties.get(option.party.id) ?? 0),
      };
    })
    .filter((line) => !line.option.isWriteIn || line.votes > 0)
    .toSorted(
      (a, b) =>
        b.votes - a.votes ||
        b.weight - a.weight ||
        Number(b.option.isIncumbent) - Number(a.option.isIncumbent) ||
        b.reach - a.reach ||
        a.index - b.index,
    );
  const contenders = (race.seats ?? 1) + 1;
  const main: Array<Line> = [];
  const rest: Array<Line> = [];
  for (const [rank, line] of ranked.entries()) {
    const isMain =
      rank < contenders || line.weight >= 0.05 || line.option.isIncumbent;
    const entry = { option: line.option, votes: line.votes, share: line.share };
    if (isMain && main.length < limit) {
      main.push(entry);
    } else {
      rest.push(entry);
    }
  }
  return { main, rest };
}

export function others(rest: Array<Line>) {
  const names = rest.map((line) => surname(line.option));
  const listed =
    names.length <= 3
      ? names
      : [
          ...names.slice(0, 2),
          m.night_state_card_more({ count: names.length - 2 }),
        ];
  return new Intl.ListFormat(getLocale(), { type: "conjunction" }).format(
    listed,
  );
}
