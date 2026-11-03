import type { LiveRace } from "@ea/analytics/live";
import type { Feed } from "#lib/live/board/feed.svelte.js";

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

  readonly fallback: LiveRace | null = $derived.by(() => {
    let best: { race: LiveRace; margin: number } | null = null;
    for (const summary of this.feed.summaries.values()) {
      if (
        summary.race.office === "house" ||
        summary.total === 0 ||
        summary.winner != null ||
        summary.projected != null
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

  constructor(feed: Feed, cartogram: Cartogram | null) {
    this.feed = feed;
    this.cartogram = cartogram;
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
