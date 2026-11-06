import type { LiveRace } from "@ea/analytics/live";
import type { Feed } from "#lib/live/board/feed.svelte.js";
import type { Shift } from "#lib/live/flips.js";
import type { Control } from "#lib/live/nation.js";
import type { Summary } from "#lib/live/summary.js";
import { hatch, shift } from "#lib/live/flips.js";
import { family } from "#lib/live/nation.js";
import { swatch } from "#lib/party.js";

export type Cartogram = {
  box: [number, number, number, number];
  districts: Array<{ key: string; d: string; x: number; y: number }>;
  states: Array<string>;
  labels: Array<{ x: number; y: number; text: string }>;
};

export class Desk {
  picked = $state<string | null>(null);
  unit = $state<string | null>(null);
  readonly feed: Feed;
  readonly cartogram: Cartogram | null;
  readonly control: Control | null;

  readonly fallback: LiveRace | null = $derived.by(() => {
    let best: { race: LiveRace; margin: number } | null = null;
    for (const summary of this.feed.summaries.values()) {
      if (
        summary.race.office === "house" ||
        summary.total === 0 ||
        summary.settled != null
      ) {
        continue;
      }
      if (best == null || summary.margin < best.margin) {
        best = { race: summary.race, margin: summary.margin };
      }
    }
    const races = this.feed.election.races;
    return (
      best?.race ??
      races.find((race) => race.office === "senate") ??
      races.find((race) => race.office === "governor") ??
      races[0] ??
      null
    );
  });

  readonly selected: LiveRace | null = $derived(this.#choose());

  readonly shifts: Map<string, Shift> = $derived.by(() => {
    const found = new Map<string, Shift>();
    for (const summary of this.feed.summaries.values()) {
      const changing = this.#shift(summary);
      if (changing != null) {
        found.set(summary.race.id, changing);
      }
    }
    return found;
  });

  constructor(
    feed: Feed,
    cartogram: Cartogram | null,
    control: Control | null,
  ) {
    this.feed = feed;
    this.cartogram = cartogram;
    this.control = control;
  }

  #shift(summary: Summary) {
    if (summary.race.isMeasure === true || summary.isShort) {
      return null;
    }
    const called = summary.settled;
    const incumbent = summary.race.options.find((option) => option.isIncumbent);
    const holder = this.control?.holders[summary.race.id];
    return shift({
      held:
        holder === undefined
          ? family(this.control, incumbent ?? null)
          : holder.party,
      to: family(this.control, called ?? summary.leader),
      isCalled: called != null,
      isCounting: summary.total > 0,
    });
  }

  hatch(summary: Summary) {
    const found = this.shifts.get(summary.race.id);
    return found == null || found.flip === "held" || summary.later != null
      ? null
      : hatch(this.party(found.to).color, found.flip);
  }

  party(id: string) {
    const found = this.control?.parties[id];
    return {
      short: found?.short ?? "–",
      color: swatch(found?.color, `party:${id}`),
    };
  }

  #choose() {
    return (
      (this.picked == null ? null : this.feed.bySlug.get(this.picked)) ??
      this.fallback
    );
  }

  select(slug: string) {
    this.picked = slug;
  }

  race(slug: string | null | undefined) {
    return slug == null ? this.selected : (this.feed.bySlug.get(slug) ?? null);
  }
}
