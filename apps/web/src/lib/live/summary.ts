import type {
  LiveElection,
  LiveEstimate,
  LiveOffice,
  LiveOption,
  LiveRace,
} from "@ea/analytics/live";
import { count, percent } from "#lib/format.js";
import { lead, stamp } from "#lib/live/format.js";
import { ranked, sum } from "#lib/live/standing.js";
import { m } from "#lib/paraglide/messages.js";

export type Summary = {
  race: LiveRace;
  estimate: LiveEstimate | null;
  totals: Array<number>;
  total: number;
  leader: LiveOption | null;
  runnerUp: LiveOption | null;
  margin: number;
  counted: number | null;
  winner: LiveOption | null;
  projected: LiveOption | null;
  projectedAt: string | null;
  favorite: LiveOption | null;
  chance: number | null;
  at: string | null;
};

export function summarize(
  race: LiveRace,
  estimate: LiveEstimate | null,
): Summary {
  const frame = race.frames.at(-1) ?? null;
  const totals =
    race.certified?.totals ?? frame?.totals ?? race.options.map(() => 0);
  const total = sum(totals);
  const order = ranked(totals).filter(
    (index) => race.options[index]?.isWriteIn !== true,
  );
  const first = order[0];
  const second = order[1];
  const winner =
    race.options.find((option) =>
      race.certified?.winners.includes(option.id),
    ) ?? null;
  const projected =
    winner == null
      ? (race.options.find((option) => option.id === race.projected?.option) ??
        null)
      : null;
  let counted: number | null = null;
  if (race.certified != null) {
    counted = 1;
  } else if (
    estimate != null &&
    (estimate.status === "estimating" || estimate.status === "too-early")
  ) {
    counted = Math.min(1, estimate.shareIn.mid);
  } else if (
    frame?.reporting != null &&
    frame.expected != null &&
    frame.expected > 0
  ) {
    counted = Math.min(1, frame.reporting / frame.expected);
  }
  const leader =
    total > 0 && first != null ? (race.options[first] ?? null) : null;
  let favorite: LiveOption | null = null;
  if (estimate?.status === "estimating" && winner == null) {
    for (const option of race.options) {
      if (
        (estimate.winProbability[option.id] ?? 0) >
        (favorite == null ? 0 : (estimate.winProbability[favorite.id] ?? 0))
      ) {
        favorite = option;
      }
    }
  }
  return {
    race,
    estimate,
    totals,
    total,
    leader,
    runnerUp:
      total > 0 && second != null ? (race.options[second] ?? null) : null,
    margin:
      total > 0
        ? ((totals[first ?? 0] ?? 0) - (totals[second ?? 1] ?? 0)) / total
        : 0,
    counted,
    winner,
    projected,
    projectedAt: projected == null ? null : (race.projected?.at ?? null),
    favorite,
    chance:
      favorite == null ? null : (estimate?.winProbability[favorite.id] ?? null),
    at: frame?.at ?? null,
  };
}

export function closing(election: LiveElection, race: LiveRace) {
  return Date.parse(
    election.pollClose.find((entry) => entry.state === race.state)?.at ??
      `${election.date}T23:59:00Z`,
  );
}

export function describe(summary: Summary, closes: number, time: number) {
  const { leader, winner, margin, total, estimate } = summary;
  if (winner != null) {
    return m.night_head_won({ name: winner.label, margin: lead(margin) });
  }
  if (summary.projected != null) {
    return m.night_head_projected({
      name: summary.projected.label,
      time: stamp(summary.projectedAt),
    });
  }
  if (total === 0 || leader == null) {
    return time < closes
      ? m.night_head_before({ time: stamp(closes) })
      : m.night_head_waiting();
  }
  if (margin < 0.0005) {
    return m.night_head_tied({ count: count(total) });
  }
  if (estimate?.status === "estimating") {
    return m.night_head_estimating({
      name: leader.label,
      margin: lead(margin),
      share: percent(Math.min(1, estimate.shareIn.mid), 0),
    });
  }
  return m.night_head_counting({
    name: leader.label,
    margin: lead(margin),
    count: count(total),
  });
}

export function party(option: LiveOption | null) {
  return option?.party?.id ?? (option == null ? "" : `candidate:${option.id}`);
}

export function tally(summaries: Array<Summary>, office: LiveOffice) {
  const groups = new Map<
    string,
    { option: LiveOption; won: number; leading: number }
  >();
  let waiting = 0;
  for (const summary of summaries) {
    if (summary.race.office !== office) {
      continue;
    }
    const option = summary.winner ?? summary.leader;
    if (option == null) {
      waiting += 1;
      continue;
    }
    const key = party(option);
    const group = groups.get(key) ?? { option, won: 0, leading: 0 };
    if (summary.winner == null) {
      group.leading += 1;
    } else {
      group.won += 1;
    }
    groups.set(key, group);
  }
  return {
    groups: [...groups.values()].toSorted(
      (a, b) => b.won + b.leading - (a.won + a.leading),
    ),
    waiting,
  };
}
