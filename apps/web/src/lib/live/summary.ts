import type {
  LiveElection,
  LiveEstimate,
  LiveOffice,
  LiveOption,
  LiveRace,
} from "@ea/analytics/live";
import { count, percent } from "#lib/format.js";
import { bandFor, lead, stamp, surname, tone } from "#lib/live/format.js";
import { isContested, ranked, sum } from "#lib/live/standing.js";
import { m } from "#lib/paraglide/messages.js";
import { leadSafety, marginShade } from "#lib/shade.js";

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
  unopposed: LiveOption | null;
  settled: LiveOption | null;
  favorite: LiveOption | null;
  chance: number | null;
  at: string | null;
  later: "none" | "outside" | null;
  isShort: boolean;
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
  const unopposed =
    race.certified == null && projected == null
      ? (race.options.find((option) =>
          race.unopposed?.options.includes(option.id),
        ) ?? null)
      : null;
  let counted: number | null = null;
  if (race.certified != null) {
    counted = 1;
  } else if (
    estimate != null &&
    (estimate.status === "estimating" ||
      estimate.status === "too-early" ||
      estimate.status === "ranked")
  ) {
    counted = Math.min(0.99, estimate.shareIn.mid);
  } else if (
    frame?.reporting != null &&
    frame.expected != null &&
    frame.expected > 0
  ) {
    counted = Math.min(0.99, frame.reporting / frame.expected);
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
    unopposed,
    settled: winner ?? projected ?? unopposed,
    favorite,
    chance:
      favorite == null ? null : (estimate?.winProbability[favorite.id] ?? null),
    at: frame?.at ?? null,
    later:
      unopposed == null &&
      race.certified == null &&
      total === 0 &&
      (race.feed === "none" || race.feed === "outside")
        ? race.feed
        : null,
    isShort:
      race.isRanked === true &&
      race.certified == null &&
      projected == null &&
      unopposed == null &&
      total > 0 &&
      (totals[first ?? 0] ?? 0) <= total / 2,
  };
}

export function bandOf(summary: Summary, seats = summary.race.seats ?? 1) {
  const { race, projected, winner, unopposed } = summary;
  if (unopposed != null) {
    return bandFor(
      race.options,
      race.unopposed?.options ?? [],
      seats,
      false,
      true,
    );
  }
  return bandFor(
    race.options,
    projected == null
      ? (race.certified?.winners ?? (winner == null ? [] : [winner.id]))
      : [projected.id],
    seats,
    projected != null,
  );
}

export function isUncounted(summary: Summary) {
  return summary.unopposed != null && summary.total === 0;
}

export function edge(summary: Summary) {
  return isContested(summary.race)
    ? `+${lead(summary.margin)}`
    : m.night_state_unopposed();
}

export function closing(election: LiveElection, race: LiveRace) {
  return Date.parse(
    election.pollClose.find((entry) => entry.state === race.state)?.at ??
      `${election.date}T23:59:00Z`,
  );
}

export function cut(summary: Summary) {
  const { race, totals } = summary;
  const seats = race.seats ?? 1;
  const order = ranked(totals).filter(
    (index) => race.options[index]?.isWriteIn !== true,
  );
  const inside = order[seats - 1];
  const outside = order[seats];
  const last = inside == null ? null : race.options[inside];
  const next = outside == null ? null : race.options[outside];
  if (
    seats < 2 ||
    race.isMeasure === true ||
    inside == null ||
    outside == null ||
    last == null ||
    next == null
  ) {
    return null;
  }
  return {
    seats,
    order,
    last,
    next,
    lead: (totals[inside] ?? 0) - (totals[outside] ?? 0),
  };
}

export function seatsUnopposedHead(seats: number, candidates: number) {
  return candidates === 1
    ? m.night_head_seats_unopposed_one({ seats: count(seats) })
    : m.night_head_seats_unopposed({
        seats: count(seats),
        count: count(candidates),
      });
}

export function unopposedHead({ race, unopposed }: Summary) {
  const seats = race.seats ?? 1;
  return seats > 1
    ? seatsUnopposedHead(seats, race.unopposed?.options.length ?? 0)
    : m.night_head_unopposed({ name: unopposed?.label ?? "" });
}

export function describe(
  summary: Summary,
  closes: number,
  time: number,
  when = stamp,
) {
  const { leader, winner, margin, total, estimate } = summary;
  const line = total > 0 ? cut(summary) : null;
  if (line != null) {
    const values = {
      name: line.last.label,
      seats: count(line.seats),
      lead: count(line.lead),
      next: line.next.label,
    };
    return summary.race.certified == null
      ? m.night_head_seats_holding(values)
      : m.night_head_seats_filled(values);
  }
  if (winner != null) {
    return isContested(summary.race)
      ? m.night_head_won({ name: winner.label, margin: lead(margin) })
      : m.night_races_won({ name: winner.label });
  }
  if (summary.projected != null) {
    return m.night_head_projected({
      name: summary.projected.label,
      time: when(summary.projectedAt),
    });
  }
  if (summary.unopposed != null) {
    return unopposedHead(summary);
  }
  if (summary.later != null) {
    return m.night_head_later();
  }
  if (total === 0 || leader == null) {
    return time < closes
      ? m.night_head_before({ time: when(closes) })
      : m.night_head_waiting();
  }
  if (margin < 0.0005) {
    return m.night_head_tied({ count: count(total) });
  }
  if (summary.isShort) {
    return m.night_head_ranked({
      name: leader.label,
      margin: lead(margin),
      share: percent(
        (summary.totals[summary.race.options.indexOf(leader)] ?? 0) / total,
      ),
    });
  }
  if (estimate?.status === "estimating") {
    return m.night_head_estimating({
      name: leader.label,
      margin: lead(margin),
      share: percent(Math.min(0.99, estimate.shareIn.mid), 0),
    });
  }
  return m.night_head_counting({
    name: leader.label,
    margin: lead(margin),
    count: count(total),
  });
}

export function verdict(summary: Summary) {
  const line = summary.total > 0 ? cut(summary) : null;
  if (line != null) {
    return m.night_races_seat({
      name: surname(line.last),
      lead: count(line.lead),
    });
  }
  if (summary.winner == null && summary.projected != null) {
    return m.night_races_projected({ name: surname(summary.projected) });
  }
  if (summary.unopposed != null) {
    return m.night_state_unopposed();
  }
  const option = summary.winner ?? summary.leader;
  if (option == null) {
    return summary.later == null ? m.night_nation_waiting() : m.night_later();
  }
  if (summary.isShort) {
    return m.night_races_ranked({
      name: surname(option),
      margin: lead(summary.margin),
    });
  }
  return summary.winner == null
    ? m.night_races_leads({
        name: surname(option),
        margin: lead(summary.margin),
      })
    : m.night_races_won({ name: surname(option) });
}

export function shade(summary: Summary) {
  const option = summary.winner ?? summary.unopposed ?? summary.leader;
  if (option == null) {
    return null;
  }
  return marginShade(
    tone(option),
    leadSafety(
      summary.margin,
      summary.winner != null || summary.unopposed != null,
    ),
  );
}

export function party(option: LiveOption | null) {
  return option?.party?.id ?? (option == null ? "" : `candidate:${option.id}`);
}

export function tally(summaries: Array<Summary>, office: LiveOffice) {
  const groups = new Map<
    string,
    {
      option: LiveOption;
      won: number;
      projected: number;
      unopposed: number;
      leading: number;
      seats: number;
    }
  >();
  let waiting = 0;
  let later = 0;
  for (const summary of summaries) {
    if (summary.race.office !== office) {
      continue;
    }
    const option = summary.settled ?? summary.leader;
    if (option == null || summary.isShort) {
      if (summary.later == null) {
        waiting += 1;
      } else {
        later += 1;
      }
      continue;
    }
    const key = party(option);
    const group = groups.get(key) ?? {
      option,
      won: 0,
      projected: 0,
      unopposed: 0,
      leading: 0,
      seats: 0,
    };
    if (summary.winner != null) {
      group.won += 1;
    } else if (summary.projected != null) {
      group.projected += 1;
    } else if (summary.unopposed == null) {
      group.leading += 1;
    } else {
      group.unopposed += 1;
    }
    group.seats += 1;
    groups.set(key, group);
  }
  return {
    groups: [...groups.values()].toSorted((a, b) => b.seats - a.seats),
    waiting,
    later,
  };
}

export function quiet(minutes: number) {
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const weeks = Math.floor(days / 7);
  if (weeks > 1) {
    return m.night_note_stale_weeks({ count: count(weeks) });
  }
  if (weeks === 1) {
    return m.night_note_stale_week();
  }
  if (days > 1) {
    return m.night_note_stale_days({ count: count(days) });
  }
  if (days === 1) {
    return m.night_note_stale_day();
  }
  if (hours > 1) {
    return m.night_note_stale_hours({ count: count(hours) });
  }
  if (hours === 1) {
    return m.night_note_stale_hour();
  }
  return m.night_note_stale({ minutes: count(minutes) });
}
