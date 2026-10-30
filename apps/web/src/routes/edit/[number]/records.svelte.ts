import { SvelteMap } from "svelte/reactivity";
import type { Found, Lookup } from "#lib/workspace.js";
import { m } from "#lib/paraglide/messages.js";
import { recordKind } from "#lib/workspace.js";

export type Looked = {
  entity: Found | null;
  problem: string | null;
  isLooking: boolean;
};

export const none: Looked = { entity: null, problem: null, isLooking: false };

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

  look(value: string, kind: string | null = null): Looked {
    if (value.trim() === "") {
      return none;
    }
    const found = this.get(value);
    if (found == null) {
      return { entity: null, problem: null, isLooking: true };
    }
    if (found.state === "missing") {
      return { entity: null, problem: m.ws_record_missing(), isLooking: false };
    }
    if (found.state === "retired") {
      return { entity: null, problem: m.ws_record_retired(), isLooking: false };
    }
    if (kind != null && found.type !== kind) {
      return {
        entity: null,
        problem: m.ws_ref_wrong_kind({
          wanted: recordKind(kind),
          kind: recordKind(found.type),
        }),
        isLooking: false,
      };
    }
    return { entity: found, problem: null, isLooking: false };
  }
}
