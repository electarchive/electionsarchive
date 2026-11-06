const DELAYS = [500, 1500, 4000];
const FIRST_PROBE = 15_000;
const LAST_PROBE = 60_000;

type Entry = {
  tries: number;
  timer: ReturnType<typeof setTimeout> | null;
  attempt: () => void;
};

export class Retries {
  #entries = new Map<string, Entry>();
  #probe: ReturnType<typeof setTimeout> | null = null;
  #wait = FIRST_PROBE;
  #isStopped = false;
  #onGaps: (hasGaps: boolean) => void;

  constructor(onGaps: (hasGaps: boolean) => void) {
    this.#onGaps = onGaps;
  }

  get isEmpty() {
    return this.#entries.size === 0;
  }

  failed(name: string, error: unknown, attempt: () => void) {
    if (this.#isStopped) {
      return;
    }
    const entry = this.#entries.get(name) ?? { tries: 0, timer: null, attempt };
    this.#entries.set(name, entry);
    entry.attempt = attempt;
    entry.tries += 1;
    if (entry.timer != null) {
      clearTimeout(entry.timer);
      entry.timer = null;
    }
    const delay = DELAYS[entry.tries - 1];
    if (delay != null) {
      if (entry.tries === 1) {
        console.warn(`${name} failed, retrying:`, error);
      }
      entry.timer = setTimeout(
        () => {
          entry.timer = null;
          entry.attempt();
        },
        delay * (0.75 + Math.random() / 2),
      );
      return;
    }
    if (entry.tries === DELAYS.length + 1) {
      console.error(`${name} failed after ${DELAYS.length} retries:`, error);
    }
    this.#onGaps(true);
    this.#schedule();
  }

  worked(name: string) {
    const entry = this.#entries.get(name);
    if (entry == null) {
      return;
    }
    const wasStranded = entry.tries > DELAYS.length;
    this.forget(name);
    if (wasStranded) {
      this.wake();
    }
  }

  forget(name: string) {
    const entry = this.#entries.get(name);
    if (entry?.timer != null) {
      clearTimeout(entry.timer);
    }
    this.#entries.delete(name);
    if (this.#stranded().length > 0) {
      return;
    }
    this.#onGaps(false);
    this.#wait = FIRST_PROBE;
    if (this.#probe == null) {
      return;
    }
    clearTimeout(this.#probe);
    this.#probe = null;
  }

  wake() {
    for (const [, entry] of this.#stranded()) {
      entry.attempt();
    }
  }

  stop() {
    this.#isStopped = true;
    for (const name of this.#entries.keys()) {
      this.forget(name);
    }
  }

  #stranded() {
    return [...this.#entries].filter(
      ([, entry]) => entry.tries > DELAYS.length,
    );
  }

  #schedule() {
    if (this.#probe != null) {
      return;
    }
    this.#probe = setTimeout(() => {
      this.#probe = null;
      this.#wait = Math.min(this.#wait * 2, LAST_PROBE);
      const [first] = this.#stranded();
      if (first == null) {
        return;
      }
      this.#schedule();
      if (document.hidden) {
        return;
      }
      const [name, entry] = first;
      this.#entries.delete(name);
      this.#entries.set(name, entry);
      entry.attempt();
    }, this.#wait);
  }
}
