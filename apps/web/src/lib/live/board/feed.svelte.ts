import type { LiveElection, LiveEstimate, LiveRace } from "@ea/analytics/live";
import { SvelteMap } from "svelte/reactivity";
import type { Update } from "#lib/live/board.js";
import type { Summary } from "#lib/live/summary.js";
import { latest, opened, recent } from "#lib/live/board.js";
import { forecast, rehearse } from "#lib/live/board/rehearse.js";
import { summarize } from "#lib/live/summary.js";
import { orpc } from "#lib/rpc.js";

export type Night = {
  election: LiveElection;
  estimates: Record<string, LiveEstimate>;
  shown: { results: boolean; forecasts: boolean };
  updates: Array<Update>;
};

export type Shapes = {
  box: [number, number, number, number];
  areas: Record<string, string>;
  codes: Record<string, string>;
};

export class Feed {
  clock = $state(Date.now());
  isOffline = $state(false);
  readonly isNation: boolean;
  readonly #started: number | null;
  #base: Night;
  readonly #states = new SvelteMap<string, Night>();
  readonly #shapes = new SvelteMap<string, Shapes>();
  readonly #asked = new Set<string>();

  readonly #quantum = $derived(Math.floor(this.clock / 10_000) * 10_000);

  readonly night: Night = $derived(this.#replay());

  readonly summaries: Map<string, Summary> = $derived(
    new Map(
      this.night.election.races.map((race) => [
        race.id,
        summarize(race, this.night.estimates[race.id] ?? null),
      ]),
    ),
  );

  readonly bySlug: Map<string, LiveRace> = $derived(
    new Map(this.night.election.races.map((race) => [race.slug, race])),
  );

  constructor(night: Night, isRehearsal: boolean) {
    this.#base = $state.raw(night);
    this.isNation = night.election.slug.split("/").length === 2;
    this.#started = isRehearsal ? Date.now() : null;
  }

  #replay() {
    return this.#play(this.#base);
  }

  get election() {
    return this.night.election;
  }

  get shown() {
    return this.night.shown;
  }

  get isRehearsal() {
    return this.#started != null;
  }

  #play(night: Night): Night {
    if (this.#started == null) {
      return night;
    }
    const election = rehearse(night.election, this.#started, this.#quantum);
    return {
      election,
      estimates: night.shown.forecasts
        ? forecast(election, this.#started, this.#quantum)
        : {},
      shown: night.shown,
      updates: recent(election.races),
    };
  }

  statePath(race: LiveRace) {
    const [country, date] = this.#base.election.slug.split("/", 2);
    return this.isNation
      ? `${country ?? "us"}/${race.state}/${date ?? ""}`
      : this.#base.election.slug;
  }

  detail(race: LiveRace): Summary {
    if (race.units.length > 0 || !this.isNation) {
      return this.summaries.get(race.id) ?? summarize(race, null);
    }
    const loaded = this.#states.get(race.state);
    if (loaded == null) {
      return this.summaries.get(race.id) ?? summarize(race, null);
    }
    const played = this.#play(loaded);
    const found = played.election.races.find((entry) => entry.id === race.id);
    return found == null
      ? (this.summaries.get(race.id) ?? summarize(race, null))
      : summarize(found, played.estimates[found.id] ?? null);
  }

  hasDetail(race: LiveRace) {
    return (
      race.units.length > 0 || !this.isNation || this.#states.has(race.state)
    );
  }

  want(race: LiveRace) {
    if (!this.isNation || race.units.length > 0) {
      return;
    }
    const key = `state:${race.state}`;
    if (this.#asked.has(key)) {
      return;
    }
    this.#asked.add(key);
    void this.#fetchState(race.state, this.statePath(race));
  }

  async #fetchNight(path: string): Promise<Night> {
    const { shown, updates, ...parts } = await latest(path);
    const { election, estimates } = await opened(parts);
    return { election, estimates, shown, updates };
  }

  async #fetchState(state: string, path: string) {
    try {
      this.#states.set(state, await this.#fetchNight(path));
    } catch {
      this.#asked.delete(`state:${state}`);
    }
  }

  shapes(path: string) {
    return this.#shapes.get(path) ?? null;
  }

  wantShapes(path: string) {
    const key = `shapes:${path}`;
    if (this.#asked.has(key)) {
      return;
    }
    this.#asked.add(key);
    void (async () => {
      try {
        const response = await fetch(`/live/shapes/${path}`);
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        const found: Shapes = await response.json();
        this.#shapes.set(path, found);
      } catch {
        this.#asked.delete(key);
      }
    })();
  }

  async #refresh() {
    const path = this.#base.election.slug;
    const probe = await orpc.live.stamp.call({ path });
    this.isOffline = false;
    if (
      probe.newest <= (this.#base.election.newest ?? "") &&
      probe.isCertified === this.#base.election.isCertified
    ) {
      return;
    }
    this.#base = await this.#fetchNight(path);
    const [country, date] = path.split("/", 2);
    await Promise.all(
      [...this.#states.keys()].map(async (state) =>
        this.#fetchState(state, `${country ?? "us"}/${state}/${date ?? ""}`),
      ),
    );
  }

  start() {
    const tick = setInterval(() => {
      this.clock = Date.now();
    }, 1000);
    const poll = setInterval(() => {
      if (this.#started != null) {
        return;
      }
      this.#refresh().catch(() => {
        this.isOffline = true;
      });
    }, 30_000);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
  }
}
