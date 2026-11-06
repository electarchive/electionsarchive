import type { LiveElection, LiveEstimate } from "@ea/analytics/live";
import { part } from "svelte-islands/server";
import type { Control } from "#lib/live/nation.js";
import { split } from "#lib/live/board.js";

const made = new WeakMap<
  LiveElection,
  {
    isTrimmed: boolean;
    control: Control | null;
    estimates: Record<string, LiveEstimate>;
  } & ReturnType<typeof split>
>();

export function liveParts(
  election: LiveElection,
  estimates: Record<string, LiveEstimate>,
  control: Control | null,
  isFull = false,
) {
  const isTrimmed = !isFull && election.slug.split("/").length === 2;
  let found = made.get(election);
  if (
    found?.isTrimmed !== isTrimmed ||
    found.control !== control ||
    found.estimates !== estimates
  ) {
    found = {
      isTrimmed,
      control,
      estimates,
      ...split(election, estimates, control, isTrimmed),
    };
    made.set(election, found);
  }
  return {
    board: {
      head: found.head,
      stable: part("live.races", found.stable, { fetch: "preload" }),
      changing: part("live.counts", found.changing, { fetch: "preload" }),
    },
    updates: found.updates,
  };
}
