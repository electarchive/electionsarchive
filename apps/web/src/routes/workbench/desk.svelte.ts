import type { ViewSpec } from "@ea/view-spec";
import { decodeId } from "@ea/view-spec";
import { SvelteSet } from "svelte/reactivity";
import { orpc } from "#lib/rpc.js";

export type Contest = Awaited<ReturnType<typeof orpc.contests.get.call>>;
export type Entry = NonNullable<Contest["places"]>["legend"][number];

export class Desk {
  focus = $state<string | null>(null);
  hovered = $state<string | null>(null);
  readonly picked = new SvelteSet<string>();
  readonly #spec: () => ViewSpec | null;

  constructor(spec: () => ViewSpec | null) {
    this.#spec = spec;
  }

  get spec() {
    return this.#spec();
  }

  get main() {
    return this.spec?.data.contests[0] ?? null;
  }

  input(contest: string | undefined) {
    const spec = this.spec;
    const compare = spec?.data.compare;
    const isMain = contest == null || contest === this.main;
    return {
      id: decodeId(contest ?? this.main ?? ""),
      by: isMain ? spec?.data.level : undefined,
      vs:
        compare == null
          ? undefined
          : "previous" in compare || !isMain
            ? ("previous" as const)
            : decodeId(compare.contests[0] ?? ""),
      match: "party" as const,
      group: "option" as const,
    };
  }

  focused(contest: Contest): Entry | null {
    const legend = contest.places?.legend ?? [];
    return (
      legend.find((entry) => entry.key === this.focus) ?? legend[0] ?? null
    );
  }

  toggle(name: string) {
    if (this.picked.has(name)) {
      this.picked.delete(name);
    } else {
      this.picked.add(name);
    }
  }
}

export function span(contest: Contest) {
  const target = contest.target;
  const own = contest.election;
  if (
    target?.date == null ||
    target.year == null ||
    own.date == null ||
    own.year == null
  ) {
    return null;
  }
  const forward = target.date > own.date;
  return {
    from: String(forward ? own.year : target.year),
    to: String(forward ? target.year : own.year),
    forward,
  };
}

export async function located(value: string) {
  if (/^[0-9a-f-]{36}$/u.test(value)) {
    return value;
  }
  const found = await orpc.contests.locate.call({ path: value });
  return found.id;
}
