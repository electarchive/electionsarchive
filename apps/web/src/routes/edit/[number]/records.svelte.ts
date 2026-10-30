import { SvelteMap } from "svelte/reactivity";
import type { Lookup } from "#lib/workspace.js";

export class Records {
  readonly #map: SvelteMap<string, Lookup | null>;

  constructor(initial: Record<string, Lookup>, refs: () => Array<string>) {
    this.#map = new SvelteMap(Object.entries(initial));

    $effect(() => {
      const wanted = refs()
        .map((ref) => ref.trim())
        .filter(
          (ref, index, all) =>
            ref !== "" && all.indexOf(ref) === index && !this.#map.has(ref),
        );
      if (wanted.length === 0) {
        return;
      }
      const timer = setTimeout(() => void this.#look(wanted), 400);
      return () => clearTimeout(timer);
    });
  }

  async #look(refs: Array<string>) {
    for (const ref of refs) {
      this.#map.set(ref, null);
    }
    const response = await fetch("/edit/records", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refs }),
    });
    const found = response.ok
      ? ((await response.json()) as Record<string, Lookup>)
      : {};
    for (const ref of refs) {
      this.#map.set(ref, found[ref] ?? { state: "missing" });
    }
  }

  get(ref: string) {
    return this.#map.get(ref.trim());
  }
}
