import type {
  LiveElection,
  LiveEstimate,
  LiveOption,
  LiveRace,
} from "@ea/analytics/live";
import type { Control, Family, Seat } from "#lib/live/nation.js";
import type { UnitStanding } from "#lib/live/standing.js";
import type { Summary } from "#lib/live/summary.js";
import { hatch } from "#lib/live/flips.js";
import { tone } from "#lib/live/format.js";
import { chamber, seat, sides, STATES } from "#lib/live/nation.js";
import { ranked, sum } from "#lib/live/standing.js";
import { summarize } from "#lib/live/summary.js";
import { swatch } from "#lib/party.js";
import {
  leadSafety,
  marginSafety,
  marginShade,
  safety,
  swingSafety,
} from "#lib/shade.js";

export type Office = "senate" | "governor" | "house";
export type Paint = "lead" | "chance" | "counted";
export type Lens = "lead" | "shift" | "last" | "counted" | "votes";
export type Fill = { color: string; isLater?: boolean; stripe?: string | null };
export type Dot = { areaId: string; value: number; color: string };
export type Swing = {
  areaId: string;
  swing: number;
  color: string;
  weight: number;
};

export const OFFICES: Array<Office> = ["senate", "governor", "house"];
export const PAINTS: Array<Paint> = ["lead", "counted", "chance"];
export const LENSES: Array<Lens> = [
  "lead",
  "shift",
  "last",
  "counted",
  "votes",
];

export const INSETS = new Set(["ak", "hi"]);
export const TERRITORIES = ["pr", "gu", "vi", "mp", "as"];

export const MUTED =
  "color-mix(in oklab, var(--muted-foreground) 7%, var(--background))";
export const WAITING =
  "color-mix(in oklab, var(--muted-foreground) 20%, var(--background))";
export const LATER =
  "color-mix(in oklab, var(--muted-foreground) 5%, var(--background))";
export const QUIET =
  "color-mix(in oklab, var(--muted-foreground) 16%, var(--background))";
export const GROUND =
  "color-mix(in oklab, var(--muted-foreground) 5%, var(--background))";
export const BLANK =
  "color-mix(in oklab, var(--muted-foreground) 14%, var(--background))";

export function reporting(share: number | null) {
  return share == null
    ? MUTED
    : marginShade("var(--map-ink)", safety(0.1 + 0.78 * share));
}

export function districtKey(race: LiveRace) {
  return race.office === "house" && race.district != null
    ? `${race.state}-${Math.trunc(Number(race.district))}`
    : null;
}

export function tail(race: LiveRace) {
  return race.slug.split("/").at(-1) ?? "";
}

export function board(
  election: LiveElection,
  estimates: Record<string, LiveEstimate>,
  control: Control | null,
) {
  const summaries = election.races.map((race) =>
    summarize(race, estimates[race.id] ?? null),
  );
  const pair = sides(control);
  const seats = new Map<string, Seat>();
  if (pair != null) {
    for (const summary of summaries) {
      if (summary.race.office !== "other") {
        seats.set(summary.race.id, seat(summary, control, pair));
      }
    }
  }
  const byState = Map.groupBy(summaries, (summary) => summary.race.state);
  const districts = new Map<string, Summary>();
  for (const summary of summaries) {
    const key = districtKey(summary.race);
    if (key != null) {
      districts.set(key, summary);
    }
  }
  const of = (office: Office) =>
    [...seats.values()].filter(
      (entry) =>
        entry.summary.race.office === office &&
        STATES.has(entry.summary.race.state),
    );
  const chambers =
    pair == null
      ? null
      : {
          senate: chamber(
            of("senate"),
            "senate",
            control?.holdovers.senate ?? {},
            pair,
          ),
          house: chamber(of("house"), "house", {}, pair),
          governor: chamber(
            of("governor"),
            "governor",
            control?.holdovers.governor ?? {},
            pair,
          ),
        };
  return { summaries, pair, seats, byState, districts, chambers };
}

export type Board = ReturnType<typeof board>;

export function tint(
  pair: [Family, Family] | null,
  control: Control | null,
  id: string | null,
) {
  if (id == null) {
    return WAITING;
  }
  for (const side of pair ?? []) {
    if (side.id === id) {
      return side.color;
    }
  }
  return swatch(control?.parties[id]?.color, `party:${id}`);
}

export function turnOf(entry: Seat | undefined) {
  const found = entry?.shift;
  return found == null || found.flip === "held"
    ? null
    : { to: found.to, flip: found.flip };
}

export function seatHatch(
  summary: Summary,
  entry: Seat | undefined,
  paint: Paint,
  pair: [Family, Family] | null,
  control: Control | null,
) {
  const turn = turnOf(entry);
  return turn == null || summary.later != null || paint === "counted"
    ? null
    : hatch(tint(pair, control, turn.to), turn.flip);
}

export function shade(
  summary: Summary,
  entry: Seat | undefined,
  paint: Paint,
  pair: [Family, Family] | null,
  control: Control | null,
) {
  if (summary.later != null) {
    return LATER;
  }
  if (paint === "counted") {
    return reporting(summary.counted ?? (summary.total > 0 ? 0.02 : null));
  }
  const mark = seatHatch(summary, entry, paint, pair, control);
  if (mark != null) {
    return mark.fill;
  }
  const called = summary.winner ?? summary.projected;
  if (entry == null || paint === "lead") {
    const option = called ?? summary.leader;
    if (option == null) {
      return WAITING;
    }
    return marginShade(
      tone(option),
      leadSafety(summary.margin, called != null),
    );
  }
  if (entry.rating === "waiting" || entry.rating === "counting") {
    return summary.total > 0
      ? marginShade(tone(summary.leader ?? undefined), safety(0.22))
      : WAITING;
  }
  const isLeft = entry.left >= entry.right;
  const sure = Math.abs(entry.left - entry.right);
  return pair == null
    ? WAITING
    : marginShade(pair[isLeft ? 0 : 1].color, safety(0.18 + 0.82 * sure));
}

export function stateShade(
  races: Array<Summary>,
  office: Office,
  paint: Paint,
  found: Board,
  control: Control | null,
): Fill {
  if (races.length === 0) {
    return { color: MUTED };
  }
  if (races.every((summary) => summary.later != null)) {
    return { color: LATER, isLater: true };
  }
  if (office === "house") {
    if (paint === "counted") {
      const share = races.map(
        (summary) => summary.counted ?? (summary.total > 0 ? 0.02 : null),
      );
      const known = share.filter((value) => value != null);
      return {
        color: reporting(known.length === 0 ? null : sum(known) / known.length),
      };
    }
    const groups = new Map<string, { option: LiveOption; seats: number }>();
    for (const summary of races) {
      const option = summary.winner ?? summary.projected ?? summary.leader;
      if (option == null) {
        continue;
      }
      const key = option.party?.id ?? option.id;
      const held = groups.get(key);
      groups.set(key, { option, seats: (held?.seats ?? 0) + 1 });
    }
    const top = [...groups.values()].toSorted((a, b) => b.seats - a.seats)[0];
    return {
      color:
        top == null
          ? WAITING
          : marginShade(
              tone(top.option),
              safety(0.25 + (0.7 * top.seats) / races.length),
            ),
    };
  }
  const first = races[0];
  if (first == null) {
    return { color: MUTED };
  }
  const entry = found.seats.get(first.race.id);
  const color = shade(first, entry, paint, found.pair, control);
  return {
    color,
    stripe: seatHatch(first, entry, paint, found.pair, control)?.stripe ?? null,
  };
}

export function prior(race: LiveRace) {
  const totals = race.options.map(() => 0);
  let turnout = 0;
  for (const row of race.baseline) {
    turnout += row.turnout;
    for (const [index, share] of row.shares.entries()) {
      totals[index] = (totals[index] ?? 0) + share * row.turnout;
    }
  }
  if (turnout <= 0) {
    return null;
  }
  const shares = totals.map((value) => value / turnout);
  const order = ranked(shares);
  const first = order[0];
  const second = order[1];
  return {
    turnout,
    shares,
    leader: first ?? null,
    margin:
      first == null ? 0 : (shares[first] ?? 0) - (shares[second ?? -1] ?? 0),
  };
}

export function lastLeader(row: UnitStanding) {
  const shares = row.baseline?.shares ?? [];
  const order = ranked(shares);
  const first = order[0];
  if (first == null || (shares[first] ?? 0) <= 0) {
    return null;
  }
  return {
    index: first,
    margin: (shares[first] ?? 0) - (shares[order[1] ?? -1] ?? 0),
  };
}

export function isRightward(
  options: Array<LiveOption>,
  [first, second]: [number, number],
) {
  const isRight = (index: number) =>
    /republican/iu.test(options[index]?.party?.name ?? "");
  return isRight(first) && !isRight(second);
}

export function swingOf(
  areaId: string,
  row: UnitStanding,
  options: Array<LiveOption>,
  pair: [number, number],
): Swing | null {
  if (row.total === 0 || row.signed == null || row.baseSigned == null) {
    return null;
  }
  const shift = row.signed - row.baseSigned;
  return {
    areaId,
    swing: isRightward(options, pair) ? shift : -shift,
    color: tone(options[shift >= 0 ? pair[0] : pair[1]]),
    weight: row.total,
  };
}

export function unitHatch(row: UnitStanding, options: Array<LiveOption>) {
  return row.flip == null || row.leader == null
    ? null
    : hatch(tone(options[row.leader]), row.flip);
}

export function unitFill(
  row: UnitStanding,
  lens: Lens,
  options: Array<LiveOption>,
  pair: [number, number],
  expected: number,
): string {
  switch (lens) {
    case "lead": {
      if (row.leader == null) {
        return BLANK;
      }
      return (
        unitHatch(row, options)?.fill ??
        marginShade(tone(options[row.leader]), marginSafety(row.margin))
      );
    }
    case "shift": {
      if (row.signed == null || row.baseSigned == null) {
        return BLANK;
      }
      const shift = row.signed - row.baseSigned;
      return marginShade(
        tone(options[shift >= 0 ? pair[0] : pair[1]]),
        swingSafety(shift),
      );
    }
    case "last": {
      const last = lastLeader(row);
      return last == null
        ? BLANK
        : marginShade(tone(options[last.index]), marginSafety(last.margin));
    }
    case "counted": {
      return row.total === 0
        ? BLANK
        : marginShade(
            "var(--map-ink)",
            safety(0.08 + 0.8 * Math.min(1, row.total / Math.max(1, expected))),
          );
    }
    default: {
      return row.total > 0 ? QUIET : BLANK;
    }
  }
}

export function holding(control: Control | null, race: LiveRace) {
  const holder = control?.holders[race.id];
  if (holder?.party == null) {
    return null;
  }
  const party = control?.parties[holder.party];
  return {
    party: party?.short ?? "",
    partyId: holder.party,
    color: swatch(party?.color ?? null, `party:${holder.party}`),
    person: holder.person,
    name: holder.name,
  };
}

export function flipped(
  entry: Seat | undefined,
  pair: [Family, Family] | null,
  control: Control | null,
) {
  const found = entry?.shift;
  if (found == null || found.flip === "held") {
    return null;
  }
  const party = (id: string) => ({
    short:
      pair?.find((family) => family.id === id)?.short ??
      control?.parties[id]?.short ??
      "",
    color: tint(pair, control, id),
  });
  return { flip: found.flip, from: party(found.from), to: party(found.to) };
}

export function seatName(race: LiveRace, states: (code: string) => string) {
  if (race.office !== "house") {
    return states(race.state);
  }
  const number = Math.trunc(Number(race.district ?? ""));
  return `${race.state.toUpperCase()}-${Number.isNaN(number) || number === 0 ? "AL" : number}`;
}

export function urgency(summary: Summary, closes: number) {
  if (summary.later != null) {
    return [4, 0];
  }
  const called = summary.winner ?? summary.projected;
  if (summary.total > 0 && called == null) {
    return [0, summary.margin];
  }
  if (called != null) {
    return [2, -summary.total];
  }
  return [1, closes];
}
