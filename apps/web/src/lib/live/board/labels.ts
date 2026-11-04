import type { LiveRace } from "@ea/analytics/live";
import type { Kind } from "#lib/live/board/layout.js";
import type { Summary } from "#lib/live/summary.js";
import { tone } from "#lib/live/format.js";
import { m } from "#lib/paraglide/messages.js";
import { leadSafety, marginShade } from "#lib/shade.js";

export const MUTED =
  "color-mix(in oklab, var(--muted-foreground) 8%, var(--background))";
export const WAITING =
  "color-mix(in oklab, var(--muted-foreground) 22%, var(--background))";

export function short(race: LiveRace) {
  const state = race.state.toUpperCase();
  if (race.office === "house") {
    return race.district == null || race.district === "0"
      ? m.board_race_at_large({ state })
      : m.board_race_house({ state, district: race.district });
  }
  if (race.office === "senate") {
    return race.slug.endsWith("special")
      ? m.board_race_special({ state })
      : m.board_race_senate({ state });
  }
  if (race.office === "governor") {
    return m.board_race_governor({ state });
  }
  return race.title;
}

export function called(summary: Summary | undefined) {
  return summary?.winner ?? summary?.projected ?? null;
}

export function ahead(summary: Summary | undefined) {
  return called(summary) ?? summary?.leader ?? null;
}

export function strength(summary: Summary | undefined) {
  const option = ahead(summary);
  if (summary == null || option == null) {
    return WAITING;
  }
  return marginShade(
    tone(option),
    leadSafety(summary.margin, called(summary) != null),
  );
}

export function districtKey(race: LiveRace) {
  return `${race.state}-${Number(race.district ?? 0)}`;
}

export const KIND_TEXT: Record<
  Kind,
  { name: () => string; about: () => string }
> = {
  race: { name: m.board_kind_race, about: m.board_kind_race_about },
  map: { name: m.board_kind_map, about: m.board_kind_map_about },
  nation: { name: m.board_kind_nation, about: m.board_kind_nation_about },
  hexes: { name: m.board_kind_hexes, about: m.board_kind_hexes_about },
  power: { name: m.board_kind_power, about: m.board_kind_power_about },
  chance: { name: m.board_kind_chance, about: m.board_kind_chance_about },
  trend: { name: m.board_kind_trend, about: m.board_kind_trend_about },
  counties: { name: m.board_kind_counties, about: m.board_kind_counties_about },
  closest: { name: m.board_kind_closest, about: m.board_kind_closest_about },
  flips: { name: m.board_kind_flips, about: m.board_kind_flips_about },
  seats: { name: m.board_kind_seats, about: m.board_kind_seats_about },
  ticker: { name: m.board_kind_ticker, about: m.board_kind_ticker_about },
  closings: { name: m.board_kind_closings, about: m.board_kind_closings_about },
  note: { name: m.board_kind_note, about: m.board_kind_note_about },
};
